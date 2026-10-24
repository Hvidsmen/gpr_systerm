"""Reviewed administrator purge of position and equipment catalogues."""
import hashlib,json
from django.apps import apps
from django.contrib import messages
from django.contrib.admin.utils import NestedObjects
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import render,redirect
from core.permissions import require_roles
from apps.planning.deletion_graph import DeletionGraph,ROOT_MODELS
from apps.planning.models import ResourceMonthAllocation,WorkResourceAssignment,GlobalPlanVersion,GlobalPlanReview,ProjectPlanMember,DailyScheduleEntry
from apps.production.models import LaborFact,LaborPlan,EquipmentFact,EquipmentPlan,EmployeeActivity,EquipmentActivity
from .models import Brigade,Position,PositionPlanningBrigade,BrigadeMerge,EquipmentType,Equipment,EquipmentTypeMerge,EquipmentCrewPosition,Employee

SALT='resources.catalogue-purge.v1'
KINDS={'brigades':(Brigade,'Должности','resources:brigade_list'),'equipment':(EquipmentType,'Техника','resources:equipment_type_list')}

class ResourcePurge:
    def __init__(self,user,kind,ids=None):
        require_roles(user,{'ADMIN'})
        if kind not in KINDS:raise PermissionDenied
        self.user=user;self.kind=kind;self.model,self.title,self.back=KINDS[kind]
        self.requested=None if ids is None else sorted(set(map(int,ids)))
        query=self.model.objects.filter(company=user.company)
        if ids is not None:query=query.filter(pk__in=self.requested)
        self.ids=set(query.values_list('pk',flat=True))
        if ids is not None and self.ids!=set(self.requested):raise PermissionDenied('Некоторые записи недоступны или уже удалены.')
        merge=BrigadeMerge if kind=='brigades' else EquipmentTypeMerge
        while True:
            sources=set(merge.objects.filter(target_id__in=self.ids).values_list('source_id',flat=True))
            if kind=='equipment':sources.update(EquipmentType.objects.filter(parent_id__in=self.ids).values_list('pk',flat=True))
            if sources<=self.ids:break
            self.ids|=sources
        self.records=list(self.model.objects.filter(pk__in=self.ids).order_by('name','pk'))
        if any(row.company_id!=user.company_id for row in self.records):raise PermissionDenied('Связанные записи другой компании.')
        self.positions=set()
        self.units=set()
        if kind=='brigades':
            self.positions=set(PositionPlanningBrigade.objects.filter(brigade_id__in=self.ids).values_list('position_id',flat=True))
            if ids is None:self.positions.update(Position.objects.filter(company=user.company).values_list('pk',flat=True))
        else:self.units=set(Equipment.objects.filter(type_id__in=self.ids).values_list('pk',flat=True))
        if Position.objects.filter(pk__in=self.positions).exclude(company=user.company).exists():raise PermissionDenied('Связанные должности другой компании.')
        targets={Brigade:self.ids if kind=='brigades' else set(),Position:self.positions,EquipmentType:self.ids if kind=='equipment' else set(),Equipment:self.units}
        for model in apps.get_models():
            if not any(f.name=='company' for f in model._meta.fields):continue
            for field in model._meta.fields:
                if field.is_relation and field.related_model in targets and targets[field.related_model] and model.objects.filter(**{field.attname+'__in':targets[field.related_model]}).exclude(company=user.company).exists():raise PermissionDenied('Связанные записи другой компании.')
        versions=set();schedules=set();projects=set();rotations=set()
        field='brigade_id' if kind=='brigades' else 'equipment_type_id'
        versions.update(ResourceMonthAllocation.objects.filter(**{field+'__in':self.ids}).values_list('version_id',flat=True))
        assignments=WorkResourceAssignment.objects.filter(position_id__in=self.positions) if kind=='brigades' else WorkResourceAssignment.objects.filter(equipment_type_id__in=self.ids)
        self.assignments=assignments
        versions.update(assignments.exclude(version=None).values_list('version_id',flat=True));schedules.update(assignments.exclude(schedule=None).values_list('schedule_id',flat=True))
        for assignment in WorkResourceAssignment.objects.filter(company=user.company).only('version_id','schedule_id','crew_snapshot').iterator(chunk_size=10):
            if self.references(assignment.crew_snapshot):
                if assignment.version_id:versions.add(assignment.version_id)
                if assignment.schedule_id:schedules.add(assignment.schedule_id)
        for model,owner in [(GlobalPlanVersion,'pk'),(GlobalPlanReview,'version_id'),(ProjectPlanMember,'consolidated_version_id')]:
            for record in model.objects.filter(company=user.company).only(owner,'snapshot').iterator(chunk_size=10):
                if self.references(record.snapshot):(projects if model is ProjectPlanMember else versions).add(getattr(record,owner))
        for entry in DailyScheduleEntry.objects.filter(schedule__company=user.company).only('schedule_id','metadata','rows','original').iterator(chunk_size=10):
            if any(self.references(value) for value in [entry.metadata,entry.rows,entry.original]):schedules.add(entry.schedule_id)
        if kind=='brigades':
            from apps.rotation.models import RotationRole
            rotations.update(RotationRole.objects.filter(brigade_id__in=self.ids).values_list('plan_id',flat=True))
        self.graph=DeletionGraph(None,user,version_ids=versions,schedule_ids=schedules,project_ids=projects,rotation_ids=rotations)
        self.people=EmployeeActivity.objects.filter(company=user.company).filter(position_id__in=self.positions) if kind=='brigades' else EmployeeActivity.objects.filter(company=user.company,equipment_id__in=self.units)
        self.machines=EquipmentActivity.objects.filter(company=user.company,equipment_id__in=self.units)
        self.people_days=set(self.people.values_list('construction_object_id','date'));self.machine_days=set(self.machines.values_list('construction_object_id','date'))
        self.reproject_groups=set(self.people.filter(position__planning_mapping__company=user.company).values_list('construction_object_id','date','position__planning_mapping__brigade_id')) if kind=='equipment' else set()
        group_query=Q(pk__in=[])
        for obj,day,brigade in self.reproject_groups:group_query|=Q(construction_object_id=obj,date=day,brigade_id=brigade)
        self.reproject_totals=LaborFact.objects.filter(company=user.company).filter(group_query)
        self.labor=LaborFact.objects.filter(company=user.company,brigade_id__in=self.ids) if kind=='brigades' else LaborFact.objects.none()
        self.machine_facts=EquipmentFact.objects.filter(company=user.company,equipment_type_id__in=self.ids) if kind=='equipment' else EquipmentFact.objects.none()
        self.labor_plans=LaborPlan.objects.filter(company=user.company,brigade_id__in=self.ids) if kind=='brigades' else LaborPlan.objects.none()
        self.machine_plans=EquipmentPlan.objects.filter(company=user.company,equipment_type_id__in=self.ids) if kind=='equipment' else EquipmentPlan.objects.none()
        self.crew=EquipmentCrewPosition.objects.filter(position_id__in=self.positions) if kind=='brigades' else EquipmentCrewPosition.objects.filter(equipment_type_id__in=self.ids)
        self.detached_employees=Employee.objects.filter(company=user.company,position_id__in=self.positions)
        self.affected={'Именной факт персонала':self.people,'Именной факт техники':self.machines,'Факт по должностям':self.labor,'Факт по видам техники':self.machine_facts,'Планы по должностям':self.labor_plans,'Планы техники':self.machine_plans,'Состав экипажей':self.crew,'Сотрудники: очистка должности':self.detached_employees,'Сводный факт персонала для пересчёта':self.reproject_totals,'Сводный факт техники для пересчёта':EquipmentFact.objects.none()}
        self.counts= [{'label':name,'count':qs.count()} for name,qs in self.affected.items() if qs.exists()]

    def references(self,data):
        targets={'brigade_id':self.ids,'position_id':self.positions} if self.kind=='brigades' else {'equipment_type_id':self.ids,'equipment_id':self.units}
        stack=[data]
        while stack:
            node=stack.pop()
            if isinstance(node,dict):
                for key,ids in targets.items():
                    value=node.get(key)
                    if isinstance(value,int) and value in ids:return True
                stack.extend(value for value in node.values() if isinstance(value,(dict,list)))
            elif isinstance(node,list):stack.extend(node)
        return False

    def signature(self):
        return hashlib.sha256(json.dumps({'records':[(r.pk,r.name) for r in self.records],'positions':sorted(self.positions),'units':sorted(self.units),'plans':self.graph.signature(),'affected':{name:list(qs.order_by('pk').values_list('pk',flat=True)) for name,qs in self.affected.items()},'days':sorted((obj,str(day)) for obj,day in self.people_days|self.machine_days),'personnel_groups':sorted((obj,str(day),brigade) for obj,day,brigade in self.reproject_groups)},sort_keys=True).encode()).hexdigest()

    def token(self):return signing.dumps({'user':self.user.pk,'company':self.user.company_id,'kind':self.kind,'ids':self.requested,'signature':self.signature()},salt=SALT,compress=True)

    def perform(self):
        from apps.audit.models import AuditLog
        self.graph.delete();self.assignments.delete();self.people.delete();self.machines.delete()
        self.labor.delete();self.machine_facts.delete();self.labor_plans.delete();self.machine_plans.delete();self.crew.delete()
        if self.kind=='brigades':
            BrigadeMerge.objects.filter(Q(source_id__in=self.ids)|Q(target_id__in=self.ids)).delete()
            PositionPlanningBrigade.objects.filter(Q(brigade_id__in=self.ids)|Q(position_id__in=self.positions)).delete()
            self.detached_employees.update(position=None)
            Position.objects.filter(company=self.user.company,pk__in=self.positions).delete()
        else:EquipmentTypeMerge.objects.filter(Q(source_id__in=self.ids)|Q(target_id__in=self.ids)).delete()
        query=self.model.objects.filter(company=self.user.company,pk__in=self.ids)
        collector=NestedObjects(using=query.db);collector.collect(list(query))
        if collector.protected:raise ValidationError('Остались неизвестные связи. Удаление отменено целиком.')
        for rows in collector.data.values():
            if any(hasattr(row,'company_id') and row.company_id!=self.user.company_id for row in rows):raise PermissionDenied('Связанные записи другой компании.')
        query.delete()
        # Rebuild only personnel groups affected by removed equipment crews.
        # Other positions/types may still have legacy totals without named rows.
        for obj,day,brigade_id in self.reproject_groups:
            count=EmployeeActivity.objects.filter(company=self.user.company,construction_object_id=obj,date=day,position__planning_mapping__brigade_id=brigade_id).values('employee_id').distinct().count()
            LaborFact.objects.filter(company=self.user.company,construction_object_id=obj,date=day,brigade_id=brigade_id).delete()
            LaborFact.objects.create(company=self.user.company,construction_object_id=obj,date=day,brigade_id=brigade_id,actual_workers=count)
        AuditLog.objects.create(user=self.user,action='DELETE',entity_type=self.model._meta.label,entity_id='bulk',old_value={'ids':sorted(self.ids),'positions':sorted(self.positions),'equipment_units':sorted(self.units)},comment='Очистка справочника и связанных планов и факта')

@login_required
def purge(request,kind,selected_ids=None):
    require_roles(request.user,{'ADMIN'})
    if kind not in KINDS:raise PermissionDenied
    error=None;payload=None
    if request.method=='POST' and request.POST.get('confirm'):
        try:
            payload=signing.loads(request.POST.get('selection',''),salt=SALT,max_age=900)
            if (payload['user'],payload['company'],payload['kind'])!=(request.user.pk,request.user.company_id,kind):raise signing.BadSignature
        except (signing.BadSignature,KeyError,TypeError):
            messages.error(request,'Подтверждение недействительно. Выберите записи заново.');return redirect(KINDS[kind][2])
        selected_ids=payload['ids']
    try:
        with transaction.atomic():
            if payload:list(KINDS[kind][0].objects.select_for_update().filter(company=request.user.company).values_list('pk',flat=True))
            preview=ResourcePurge(request.user,kind,selected_ids)
            if payload:
                if request.POST.get('confirm_related')!='on':raise ValidationError('Подтвердите удаление справочника, связанных планов и факта.')
                if payload['signature']!=preview.signature():raise ValidationError('Связи изменились. Проверьте обновлённый список.')
                for label,model in ROOT_MODELS.items():list(model.objects.select_for_update().filter(pk__in=preview.graph.ids[label]).values_list('pk',flat=True))
                preview.perform();messages.success(request,f'Удалено записей: {len(preview.ids)} вместе со связанными данными.');return redirect(preview.back)
    except (ValidationError,ProtectedError) as exc:
        error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Связи изменились. Удаление отменено целиком.'
        preview=ResourcePurge(request.user,kind,selected_ids)
    return render(request,'resources/purge.html',{'preview':preview,'kind':kind,'selection':preview.token(),'error':error},status=400 if error else 200)


def selected_preview(request,kind):
    ids=request.POST.getlist('selected')
    if not ids or len(ids)>1000:raise PermissionDenied('Выберите записи или очистку всего справочника.')
    try:ids=[int(pk) for pk in ids]
    except ValueError:raise PermissionDenied('Некорректные ID.')
    return purge(request,kind,selected_ids=ids)


def single_purge(request,pk,kind):
    return purge(request,kind,selected_ids=[pk])
