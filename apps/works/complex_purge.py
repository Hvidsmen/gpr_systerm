"""Delete selected complex objects, including their entire catalogue branches."""
import hashlib,json
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import render,redirect
from core.permissions import require_roles
from apps.audit.models import AuditLog
from .models import ComplexObject,ProjectWork
from .purge import WorkPurge
from apps.planning.deletion_graph import ROOT_MODELS

SALT='works.complex-purge.v1'


def selection(user,object_ids,work_ids):
    objects=list(ComplexObject.objects.filter(company=user.company,pk__in=object_ids).order_by('pk'))
    if {row.pk for row in objects}!=set(object_ids):raise PermissionDenied('Комплексные объекты недоступны или удалены.')
    ids=set(ProjectWork.objects.filter(complex_object_id__in=object_ids).values_list('pk',flat=True))|set(work_ids)
    preview=WorkPurge(user,ids)
    signature=hashlib.sha256(json.dumps({'objects':[(row.pk,row.name,row.construction_object_id) for row in objects],'works':preview.signature()},sort_keys=True).encode()).hexdigest()
    return objects,preview,signature


@login_required
def purge_complex(request):
    require_roles(request.user,{'ADMIN'})
    if request.method!='POST':raise PermissionDenied('Выберите комплексные объекты в дереве.')
    error=None
    if request.POST.get('confirm'):
        try:
            payload=signing.loads(request.POST.get('selection',''),salt=SALT,max_age=900)
            if (payload['user'],payload['company'])!=(request.user.pk,request.user.company_id):raise signing.BadSignature
            object_ids=payload['objects'];work_ids=payload['works']
        except (signing.BadSignature,KeyError,TypeError):
            messages.error(request,'Подтверждение устарело. Выберите объекты повторно.');return redirect('works:work_list')
    else:
        payload=None
        try:
            object_ids=sorted(set(map(int,request.POST.getlist('selected_complex'))))
            work_ids=sorted(set(map(int,request.POST.getlist('selected'))))
        except ValueError:raise PermissionDenied('Некорректные идентификаторы.')
        if not object_ids or len(object_ids)+len(work_ids)>1000:raise PermissionDenied('Выберите от 1 до 1000 записей.')
    try:
        with transaction.atomic():
            if payload:
                list(ComplexObject.objects.select_for_update().filter(company=request.user.company,pk__in=object_ids))
                list(ProjectWork.objects.select_for_update().filter(company=request.user.company))
            objects,preview,signature=selection(request.user,object_ids,work_ids)
            if payload:
                if request.POST.get('confirm_related')!='on':raise ValidationError('Подтвердите удаление объектов и связанных данных.')
                if payload['signature']!=signature:raise ValidationError('Состав изменился. Проверьте обновлённый список.')
                for label,model in ROOT_MODELS.items():list(model.objects.select_for_update().filter(pk__in=preview.graph.ids[label]))
                preview.perform()
                ComplexObject.objects.filter(company=request.user.company,pk__in=object_ids).delete()
                AuditLog.objects.create(user=request.user,action='DELETE',entity_type='works.ComplexObject',entity_id='bulk',old_value={'ids':object_ids},comment='Удаление комплексных объектов со всеми работами и связанными данными')
                messages.success(request,f'Удалено комплексных объектов: {len(objects)}. Работ: {len(preview.ids)}. Связанные планы и факт удалены.')
                return redirect('works:work_list')
    except (ValidationError,ProtectedError) as exc:
        error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Обнаружены дополнительные связи. Удаление отменено целиком.'
        objects,preview,signature=selection(request.user,object_ids,work_ids)
    token=signing.dumps({'user':request.user.pk,'company':request.user.company_id,'objects':object_ids,'works':work_ids,'signature':signature},salt=SALT,compress=True)
    return render(request,'works/complex_purge.html',{'objects':objects,'preview':preview,'selection':token,'error':error},status=400 if error else 200)
