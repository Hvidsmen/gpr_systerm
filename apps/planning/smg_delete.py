"""Review and delete an editable SMG without deleting its GPR or production facts."""
import hashlib,json
from django.contrib import messages
from django.contrib.admin.utils import NestedObjects
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404,render,redirect
from django.views import View
from core.permissions import PLAN_ROLES,require_roles,require_planning_object,scope_queryset
from .models import DailySchedule,WorkTask

SALT='delete-draft-smg-v1'


def reason(schedule):
    if schedule.status!='DRAFT':return 'Удаляется только черновик СМГ. Согласуемые, утверждённые и применённые редакции сохраняют историю.'
    if schedule.locked_before:return 'В СМГ зафиксированы закрытые дни. Удаление недоступно.'
    if schedule.approval_reviews.exists():return 'В СМГ есть история согласования. Удаление недоступно.'
    if schedule.rebased_schedules.exists():return 'От этого СМГ созданы другие редакции. Сначала разберите зависимые черновики.'
    if WorkTask.objects.filter(schedule=schedule,kind='FACT').exists():return 'СМГ связан с заданиями на ввод факта. Удаление недоступно.'
    return ''


def fingerprint(schedule):
    state={'schedule':[schedule.pk,schedule.status,str(schedule.updated_at),str(schedule.locked_before)],
           'entries':list(schedule.entries.order_by('pk').values_list('pk','revision')),
           'resources':list(schedule.resource_assignments.order_by('pk').values_list('pk','updated_at')),
           'reviews':list(schedule.estimate_reviews.order_by('pk').values_list('pk','status','updated_at')),
           'tasks':list(WorkTask.objects.filter(schedule=schedule).order_by('pk').values_list('pk','status','updated_at'))}
    return hashlib.sha256(json.dumps(state,sort_keys=True,default=str).encode()).hexdigest()


def context(schedule,user,error=''):
    blocked=reason(schedule)
    collector=NestedObjects(using=schedule._state.db);collector.collect([schedule])
    if collector.protected:blocked=blocked or 'На СМГ ссылаются защищённые записи. Удаление недоступно.'
    if any(getattr(row,'company_id',schedule.company_id)!=schedule.company_id for rows in collector.data.values() for row in rows):blocked='Связанные записи относятся к другой компании.'
    return {'schedule':schedule,'reason':blocked,'error':error,'entries_count':schedule.entries.count(),'resources_count':schedule.resource_assignments.count(),
            'preview_token':signing.dumps({'user':user.pk,'company':user.company_id,'id':schedule.pk,'fingerprint':fingerprint(schedule)},salt=SALT,compress=True)}


class SMGDelete(View):
    def get(self,request,pk):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.select_related('construction_object'),request.user),pk=pk)
        return render(request,'planning/smg_delete.html',context(schedule,request.user))

    def post(self,request,pk):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
        try:
            with transaction.atomic():
                schedule=DailySchedule.objects.select_for_update().get(pk=schedule.pk,company=request.user.company)
                require_planning_object(request.user,schedule.construction_object_id)
                list(schedule.entries.select_for_update().order_by('pk').values_list('pk',flat=True))
                list(schedule.resource_assignments.select_for_update().order_by('pk').values_list('pk',flat=True))
                details=context(schedule,request.user)
                if details['reason']:raise ValidationError(details['reason'])
                try:data=signing.loads(request.POST.get('preview_token',''),salt=SALT,max_age=1800)
                except signing.BadSignature:raise ValidationError('Откройте подтверждение удаления снова.')
                if request.POST.get('confirm')!='1' or data!={'user':request.user.pk,'company':request.user.company_id,'id':schedule.pk,'fingerprint':fingerprint(schedule)}:
                    raise ValidationError('СМГ изменился после просмотра. Проверьте данные и подтвердите удаление снова.')
                from apps.audit.models import AuditLog
                AuditLog.objects.create(user=request.user,action='DELETE',entity_type='planning.dailyschedule',entity_id=str(schedule.pk),old_value={'object_id':schedule.construction_object_id,'month':str(schedule.month),'revision':schedule.revision,'entries':details['entries_count'],'resources':details['resources_count']},comment='Удалён черновик СМГ после подтверждения')
                schedule.delete()
            messages.success(request,'Черновик СМГ удалён.')
            return redirect('planning:daily_schedule_list')
        except (ValidationError,ProtectedError) as exc:
            error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Связи СМГ изменились. Проверьте их перед повторным удалением.'
            return render(request,'planning/smg_delete.html',context(schedule,request.user,error),status=400)
