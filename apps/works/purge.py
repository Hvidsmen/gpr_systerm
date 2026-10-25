"""Administrator-only, previewed deletion of work catalogues and their usage."""
import hashlib,json
from django.apps import apps
from django.contrib import messages
from django.contrib.admin.utils import NestedObjects
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.db.models.deletion import ProtectedError
from django.shortcuts import render,redirect
from core.permissions import require_roles
from apps.planning.deletion_graph import DeletionGraph,ROOT_MODELS
from apps.planning.models import ObjectWorkPlanRow,GlobalPlanVersion,GlobalPlanReview,ProjectPlanMember,DailyScheduleEntry,WorkMonthAllocation,MonthlyPlan,PlanVersion,SmgWorkMapping,MacroCarryoverWork,WorkCarryover,WorkResourceAssignment
from apps.production.models import DailyFact,EmployeeActivity,EquipmentActivity,LaborFact,EquipmentFact
from .models import ProjectWork,ProjectWorkItem,WorkMergeSource,WorkMergePlanRevision

SALT='works.purge-catalogue.v1'

def at_days(model,company,days):
    if not days:return model.objects.none()
    query=Q(pk__in=[])
    for obj,day in days:query|=Q(construction_object_id=obj,date=day)
    return model.objects.filter(company=company).filter(query)

class WorkPurge:
    def __init__(self,user,ids=None):
        require_roles(user,{'ADMIN'})
        self.user=user;self.requested=None if ids is None else sorted(set(map(int,ids)))
        query=ProjectWork.objects.filter(company=user.company)
        if self.requested is not None:query=query.filter(pk__in=self.requested)
        self.ids=set(query.values_list('pk',flat=True))
        if self.requested is not None and self.ids!=set(self.requested):raise PermissionDenied('Некоторые работы недоступны или уже удалены.')
        # Archived sources of selected merged packages also belong to their catalogue tree.
        while True:
            sources=set(WorkMergeSource.objects.filter(item__project_work_id__in=self.ids).values_list('source_work_id',flat=True))
            if sources<=self.ids:break
            self.ids|=sources
        self.works=list(ProjectWork.objects.filter(pk__in=self.ids).select_related('section__construction_object').order_by('section__construction_object__name','name','pk'))
        if any(work.company_id!=user.company_id for work in self.works):raise PermissionDenied('Обнаружена связь с другой компанией.')
        self.item_ids=set(ProjectWorkItem.objects.filter(project_work_id__in=self.ids).values_list('pk',flat=True))
        monthly_plan_ids=set(MonthlyPlan.objects.filter(project_work_id__in=self.ids).values_list('pk',flat=True))
        self.monthly_ids=set(PlanVersion.objects.filter(monthly_plan_id__in=monthly_plan_ids).values_list('pk',flat=True))
        targets={ProjectWork:self.ids,ProjectWorkItem:self.item_ids,MonthlyPlan:monthly_plan_ids,PlanVersion:self.monthly_ids}
        # Reject malformed cross-company references instead of cascading into them.
        for model in apps.get_models():
            if not any(f.name=='company' for f in model._meta.fields):continue
            for field in model._meta.fields:
                if not field.is_relation or field.related_model not in targets:continue
                target=targets[field.related_model]
                if target and model.objects.filter(**{field.attname+'__in':target}).exclude(company=user.company).exists():raise PermissionDenied('Связанные записи другой компании. Удаление отменено.')
        version_ids=set(WorkMonthAllocation.objects.filter(work_id__in=self.ids).values_list('version_id',flat=True))
        version_ids.update(GlobalPlanVersion.objects.filter(source_versions__pk__in=self.monthly_ids).values_list('pk',flat=True))
        version_ids.update(WorkCarryover.objects.filter(work_id__in=self.ids).values_list('version_id',flat=True))
        workspace_ids=set(SmgWorkMapping.objects.filter(Q(gpr_work_id__in=self.ids)|Q(smg_work_id__in=self.ids)).values_list('workspace_id',flat=True))
        workspace_ids.update(MacroCarryoverWork.objects.filter(Q(source_work_id__in=self.ids)|Q(target_work_id__in=self.ids)).values_list('workspace_id',flat=True))
        assignments=WorkResourceAssignment.objects.filter(Q(work_id__in=self.ids)|Q(work_item_id__in=self.item_ids))
        version_ids.update(assignments.exclude(version=None).values_list('version_id',flat=True))
        schedule_ids=set(assignments.exclude(schedule=None).values_list('schedule_id',flat=True))
        keys={f'work:{pk}' for pk in self.ids}
        for entry in DailyScheduleEntry.objects.filter(schedule__company=user.company,kind='work').only('schedule_id','metadata','key').iterator(chunk_size=50):
            if entry.metadata.get('id') in self.ids or entry.key in keys:schedule_ids.add(entry.schedule_id)
        for version in GlobalPlanVersion.objects.filter(company=user.company).only('pk','snapshot').iterator(chunk_size=10):
            if self.references(version.snapshot):version_ids.add(version.pk)
        for review in GlobalPlanReview.objects.filter(company=user.company).only('version_id','snapshot').iterator(chunk_size=10):
            if self.references(review.snapshot):version_ids.add(review.version_id)
        project_ids=set()
        for member in ProjectPlanMember.objects.filter(company=user.company).only('consolidated_version_id','snapshot').iterator(chunk_size=10):
            if self.references(member.snapshot):project_ids.add(member.consolidated_version_id)
        self.graph=DeletionGraph(None,user,version_ids=version_ids,workspace_ids=workspace_ids,schedule_ids=schedule_ids,project_ids=project_ids)
        self.activity_query=Q(work_id__in=self.ids)|Q(work_item_id__in=self.item_ids)
        self.people=EmployeeActivity.objects.filter(company=user.company).filter(self.activity_query)
        self.machines=EquipmentActivity.objects.filter(company=user.company).filter(self.activity_query)
        self.people_days=set(self.people.values_list('construction_object_id','date'))
        self.machine_days=set(self.machines.values_list('construction_object_id','date'))
        self.facts=DailyFact.objects.filter(company=user.company).filter(Q(project_work_id__in=self.ids)|Q(work_item_id__in=self.item_ids))
        self.summaries={'Работы в планах на весь объект':ObjectWorkPlanRow.objects.filter(company=user.company).filter(self.activity_query),'Факт работ':self.facts,'Занятость сотрудников':self.people,'Занятость техники':self.machines,'Сводный факт персонала для пересчёта':at_days(LaborFact,user.company,self.people_days),'Сводный факт техники для пересчёта':at_days(EquipmentFact,user.company,self.machine_days)}
        self.counts=[{'label':name,'count':qs.count()} for name,qs in self.summaries.items() if qs.exists()]

    def references(self,data):
        stack=[data]
        while stack:
            node=stack.pop()
            if isinstance(node,dict):
                if any(node.get(key) in self.ids for key in ['work_id','source_work_id','target_work_id','history_source_work_id'] if isinstance(node.get(key),int)):return True
                works=node.get('works');versions=node.get('versions')
                if isinstance(works,list) and any(isinstance(work,dict) and isinstance(work.get('id'),int) and work['id'] in self.ids for work in works):return True
                if isinstance(versions,list) and any(isinstance(version,dict) and isinstance(version.get('id'),int) and version['id'] in self.monthly_ids for version in versions):return True
                stack.extend(value for value in node.values() if isinstance(value,(dict,list)))
            elif isinstance(node,list):stack.extend(node)
        return False

    def signature(self):
        data={'works':[(w.pk,w.name,w.section_id,w.complex_object_id) for w in self.works],'items':sorted(self.item_ids),'plans':self.graph.signature(),'monthly_versions':list(PlanVersion.objects.filter(pk__in=self.monthly_ids).order_by('pk').values_list('pk','status')),'facts':{name:list(qs.order_by('pk').values_list('pk',flat=True)) for name,qs in self.summaries.items()},'days':sorted((obj,str(day)) for obj,day in self.people_days|self.machine_days)}
        return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()

    def token(self):
        return signing.dumps({'user':self.user.pk,'company':self.user.company_id,'ids':self.requested,'signature':self.signature()},salt=SALT,compress=True)

    def perform(self):
        from apps.audit.models import AuditLog
        from apps.production.dispatch import project_resources
        from apps.projects.models import ConstructionObject
        versions=self.graph.ids['planning.GlobalPlanVersion']
        WorkMergePlanRevision.objects.filter(company=self.user.company).filter(Q(parent_work_id__in=self.ids)|Q(source_version_id__in=versions)|Q(target_version_id__in=versions)).delete()
        self.graph.delete()
        WorkResourceAssignment.objects.filter(company=self.user.company).filter(self.activity_query).delete()
        WorkMergeSource.objects.filter(Q(source_work_id__in=self.ids)|Q(item_id__in=self.item_ids)).delete()
        self.people.delete();self.machines.delete()
        selected=ProjectWork.objects.filter(pk__in=self.ids,company=self.user.company)
        collector=NestedObjects(using=selected.db);collector.collect(list(selected))
        if collector.protected:raise ValidationError('Остались неизвестные связи справочника. Удаление отменено целиком.')
        for rows in collector.data.values():
            if any(hasattr(row,'company_id') and row.company_id!=self.user.company_id for row in rows):raise PermissionDenied('Связанные записи другой компании.')
        selected.delete()
        for section,days in [('labor',self.people_days),('mechanization',self.machine_days)]:
            for obj,day in days:project_resources(ConstructionObject.objects.get(pk=obj,company=self.user.company),day,section)
        AuditLog.objects.create(user=self.user,action='DELETE',entity_type='works.ProjectWork',entity_id='bulk',old_value={'work_ids':sorted(self.ids),'planning_dependencies':{key:sorted(ids) for key,ids in self.graph.ids.items()}},comment='Полная очистка работ и связанных планов и факта')

@login_required
def purge(request,selected_ids=None):
    require_roles(request.user,{'ADMIN'})
    error=None;payload=None
    if request.method=='POST' and request.POST.get('confirm'):
        try:
            payload=signing.loads(request.POST.get('selection',''),salt=SALT,max_age=900)
            if (payload['user'],payload['company'])!=(request.user.pk,request.user.company_id):raise signing.BadSignature
        except (signing.BadSignature,KeyError,TypeError):
            messages.error(request,'Подтверждение устарело или изменено. Выберите работы заново.');return redirect('works:work_list')
        selected_ids=payload['ids']
    try:
        with transaction.atomic():
            if payload:
                list(ProjectWork.objects.select_for_update().filter(company=request.user.company).values_list('pk',flat=True))
            preview=WorkPurge(request.user,selected_ids)
            if payload:
                if request.POST.get('confirm_related')!='on':raise ValidationError('Подтвердите удаление работ, связанных планов и факта.')
                if payload['signature']!=preview.signature():raise ValidationError('Состав связей изменился. Проверьте обновлённый список и подтвердите удаление заново.')
                for label,model in ROOT_MODELS.items():list(model.objects.select_for_update().filter(pk__in=preview.graph.ids[label]).values_list('pk',flat=True))
                preview.perform()
                messages.success(request,f'Удалено работ: {len(preview.ids)} вместе со связанными планами и фактом.')
                return redirect('works:work_list')
    except (ValidationError,ProtectedError) as exc:
        error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Связи изменились. Удаление отменено целиком.'
        preview=WorkPurge(request.user,selected_ids)
    return render(request,'works/purge.html',{'preview':preview,'selection':preview.token(),'error':error},status=400 if error else 200)


def selected_preview(request):
    if request.POST.getlist('selected_complex'):
        from .complex_purge import purge_complex
        return purge_complex(request)
    ids=request.POST.getlist('selected')
    if not ids or len(ids)>1000:raise PermissionDenied('Выберите от 1 до 1000 работ или воспользуйтесь очисткой всего справочника.')
    try:ids=[int(pk) for pk in ids]
    except ValueError:raise PermissionDenied('Некорректные ID работ.')
    return purge(request,selected_ids=ids)
