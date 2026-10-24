"""Explicit plan dependency closure. Never follow links into source catalogues/facts."""
import hashlib,json
from django.apps import apps
from django.core import signing
from django.core.exceptions import ValidationError,PermissionDenied
from django.db.models.deletion import PROTECT
from core.permissions import scope_queryset,role_code
from .models import PlanningWorkspace,GlobalPlanVersion,GlobalPlanReview,DailySchedule,ProjectPlanVersion

SALT='planning.delete-dependency-preview.v1'
ROOT_MODELS={model._meta.label:model for model in [PlanningWorkspace,GlobalPlanVersion,DailySchedule,ProjectPlanVersion,apps.get_model('rotation','RotationPlan')]}

class DeletionGraph:
    def __init__(self,workspace,user,*,version_ids=(),workspace_ids=(),schedule_ids=(),project_ids=()):
        self.workspace=workspace;self.user=user
        self.ids={label:set() for label in ROOT_MODELS}
        if workspace:self.add(PlanningWorkspace,workspace.pk)
        for pk in workspace_ids:self.add(PlanningWorkspace,pk)
        for version in GlobalPlanVersion.objects.filter(pk__in=version_ids):self.add_version(version)
        for pk in schedule_ids:self.add(DailySchedule,pk)
        for pk in project_ids:self.add(ProjectPlanVersion,pk)
        targets=set(ROOT_MODELS)|{'planning.GlobalPlanReview'}
        relations=[(model,field) for model in apps.get_models() for field in model._meta.fields if field.is_relation and field.related_model and field.related_model._meta.label in targets and field.remote_field.on_delete is PROTECT]
        while True:
            before={key:frozenset(ids) for key,ids in self.ids.items()}
            for version in GlobalPlanVersion.objects.filter(workspace_id__in=self.ids['planning.PlanningWorkspace']).only('pk','company_id','workspace_id'):self.add_version(version)
            reviews=list(GlobalPlanReview.objects.filter(version_id__in=self.ids['planning.GlobalPlanVersion']).only('pk','company_id'))
            for review in reviews:self.check_company(review)
            related={**self.ids,'planning.GlobalPlanReview':{r.pk for r in reviews}}
            for model,field in relations:
                ids=related[field.related_model._meta.label]
                if not ids:continue
                for record in model.objects.defer(*[f.name for f in model._meta.fields if f.get_internal_type()=='JSONField']).filter(**{field.attname+'__in':ids}):
                    self.check_company(record)
                    if model is GlobalPlanVersion:self.add_version(record)
                    elif model._meta.label in ROOT_MODELS:self.add(model,record.pk)
                    elif model._meta.label=='planning.ProjectPlanMember':self.add(ProjectPlanVersion,record.consolidated_version_id)
                    elif model._meta.label=='planning.MacroHistorySource':self.add(PlanningWorkspace,record.workspace_id)
                    elif model._meta.label in {'planning.WorkCarryover','planning.CarryoverReviewTask'}:
                        self.add_version(GlobalPlanVersion.objects.only('pk','company_id','workspace_id').get(pk=record.version_id))
                        if getattr(record,'correction_version_id',None):self.add_version(GlobalPlanVersion.objects.only('pk','company_id','workspace_id').get(pk=record.correction_version_id))
                    else:raise ValidationError('Неизвестная зависимость плана: '+model._meta.label)
            if before=={key:frozenset(ids) for key,ids in self.ids.items()}:break
        self.records={label:list(model.objects.defer(*[f.name for f in model._meta.fields if f.get_internal_type()=='JSONField']).filter(pk__in=self.ids[label]).order_by('pk')) for label,model in ROOT_MODELS.items()}
        for label,records in self.records.items():
            for record in records:self.check_company(record)
            if role_code(user)!='ADMIN' and set(scope_queryset(ROOT_MODELS[label].objects.filter(pk__in=self.ids[label]),user).values_list('pk',flat=True))!=self.ids[label]:
                raise PermissionDenied('В цепочке есть планы объектов, которые вам не назначены. Удаление доступно администратору.')
        self.history=bool(reviews) or any(v.status not in {'DRAFT','REJECTED'} for v in self.records['planning.GlobalPlanVersion']) or any(s.status!='DRAFT' for s in self.records['planning.DailySchedule']) or any(p.status=='FIXED' for p in self.records['planning.ProjectPlanVersion'])
        self.linked=bool(self.ids['planning.DailySchedule'] or self.ids['planning.ProjectPlanVersion'] or self.ids['rotation.RotationPlan'] or len(self.ids['planning.PlanningWorkspace'])>1 or workspace and any(v.workspace_id!=workspace.pk for v in self.records['planning.GlobalPlanVersion']))
        self.groups=[{'label':label,'records':self.records[model]} for model,label in [('planning.PlanningWorkspace','Планы на период'),('planning.GlobalPlanVersion','Версии ГПР и месячные уточнения'),('planning.DailySchedule','Дневные СМГ и их редакции'),('planning.ProjectPlanVersion','Сводные планы проекта'),('rotation.RotationPlan','Планы персонала')] if self.records[model]]

    def check_company(self,record):
        if record.company_id!=self.user.company_id:raise PermissionDenied('Обнаружена связь с другой компанией. Данные не удалены.')

    def add(self,model,pk):
        if pk:self.ids[model._meta.label].add(pk)

    def add_version(self,version):
        self.check_company(version);self.add(GlobalPlanVersion,version.pk)
        if version.workspace_id:self.add(PlanningWorkspace,version.workspace_id)

    def signature(self):
        data={key:[(r.pk,str(r),getattr(r,'status','')) for r in records] for key,records in self.records.items()}
        return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()

    def token(self):
        return signing.dumps({'root':self.workspace.pk,'user':self.user.pk,'company':self.user.company_id,'signature':self.signature()},salt=SALT)

    def verify(self,token):
        try:
            data=signing.loads(token or '',salt=SALT,max_age=900)
            valid=data=={'root':self.workspace.pk,'user':self.user.pk,'company':self.user.company_id,'signature':self.signature()}
        except signing.BadSignature:valid=False
        if not valid:raise ValidationError('Проверьте обновлённый список связанных планов и подтвердите удаление ещё раз.')

    def check_permission(self):
        if self.history and role_code(self.user)!='ADMIN':raise ValidationError('В цепочке есть согласованные планы или история согласования. Удаление доступно администратору.')

    def delete(self):
        from .deletion_context import authorized_workspace_deletion
        from apps.audit.models import AuditLog
        from .models import WorkCarryover,CarryoverReviewTask,MacroHistorySource,ProjectPlanMember
        versions=self.records['planning.GlobalPlanVersion'];projects=self.records['planning.ProjectPlanVersion']
        version_ids=self.ids['planning.GlobalPlanVersion'];workspace_ids=self.ids['planning.PlanningWorkspace']
        # Only internal protected links are broken, inside the caller's transaction.
        PlanningWorkspace.objects.filter(pk__in=workspace_ids).update(baseline_version=None,continued_from_review=None,parent_gpr=None)
        GlobalPlanVersion.objects.filter(pk__in=version_ids).update(previous_version=None,baseline_review=None)
        ProjectPlanVersion.objects.filter(pk__in=self.ids['planning.ProjectPlanVersion']).update(previous_version=None)
        DailySchedule.objects.filter(pk__in=self.ids['planning.DailySchedule']).update(replaces=None)
        with authorized_workspace_deletion(versions,project_versions=projects):
            ProjectPlanMember.objects.filter(consolidated_version_id__in=self.ids['planning.ProjectPlanVersion']).delete()
            ProjectPlanVersion.objects.filter(pk__in=self.ids['planning.ProjectPlanVersion']).delete()
            ROOT_MODELS['rotation.RotationPlan'].objects.filter(pk__in=self.ids['rotation.RotationPlan']).delete()
            DailySchedule.objects.filter(pk__in=self.ids['planning.DailySchedule']).delete()
            WorkCarryover.objects.filter(source_version_id__in=version_ids).delete()
            CarryoverReviewTask.objects.filter(source_version_id__in=version_ids).delete()
            MacroHistorySource.objects.filter(review__version_id__in=version_ids).delete()
            GlobalPlanVersion.objects.filter(pk__in=version_ids).defer('snapshot').delete()
            PlanningWorkspace.objects.filter(pk__in=workspace_ids).delete()
        AuditLog.objects.create(user=self.user,action='DELETE',entity_type='planning.PlanningWorkspace' if self.workspace else 'works.WorkPurge',entity_id=str(self.workspace.pk) if self.workspace else 'dependencies',old_value={key:sorted(ids) for key,ids in self.ids.items()},comment='Удаление плана и всей зависимой цепочки планирования')
