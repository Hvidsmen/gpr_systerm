"""Reviewable reasons and authenticated private multi-file uploads."""
from pathlib import Path
from hashlib import sha256
from uuid import UUID
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import F
from django.http import JsonResponse,FileResponse,Http404
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods,require_POST,require_GET
from core.permissions import require_roles,role_code,scope_queryset,READ_ROLES
from core.fact_edit_window import require_fact_day
from core.fact_history import history_actor
from .entry_concurrency import sqlite_serialized
from .forms import ObjectDateForm
from .models import DailyFact,ActuationReason,ActuationDocument
from .dispatch_models import WorkFactClaim
from . import actuation_reasons as service

MAX_FILE=25*1024*1024
MAX_TOTAL=100*1024*1024
MAX_COUNT=30
EXTENSIONS={'.pdf','.jpg','.jpeg','.png','.webp','.tif','.tiff','.doc','.docx','.odt','.xls','.xlsx','.xlsm','.zip','.rar','.7z'}


def day_scope(request,data,edit=False):
    require_roles(request.user,service.EDITORS if edit else service.EDITORS|READ_ROLES)
    selector=ObjectDateForm(data,user=request.user,production_read=not edit)
    if not selector.is_valid():raise ValidationError('Выберите доступный объект и дату.')
    obj,day=selector.cleaned_data['construction_object'],selector.cleaned_data['date']
    if edit:require_fact_day(request.user,day)
    return obj,day


@login_required
@require_POST
@sqlite_serialized
def propose(request):
    try:
        obj,day=day_scope(request,request.POST,True)
        reason=service.propose(request.user,request.POST.get('name',''))
        return JsonResponse({'reason':{'id':reason.pk,'label':reason.label,'status':reason.status},'message':'Причина направлена руководителю производства на проверку.' if reason.status=='PENDING' else 'Такая причина уже есть в списке.'})
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)


class DecisionForm(forms.Form):
    action=forms.ChoiceField(label='Решение',choices=[('approve','Принять новую причину'),('replace','Заменить существующей'),('reject','Удалить из списка')])
    target=forms.ModelChoiceField(label='Существующая причина',queryset=ActuationReason.objects.none(),required=False)
    comment=forms.CharField(label='Комментарий',required=False,max_length=4000,widget=forms.Textarea(attrs={'rows':3}))
    revision=forms.IntegerField(widget=forms.HiddenInput)
    def __init__(self,*args,reason,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['target'].queryset=ActuationReason.objects.filter(company=reason.company,status='APPROVED').exclude(pk=reason.pk)
        self.initial['revision']=reason.revision
        for field in self.fields.values():field.widget.attrs['class']='form-control'
    def clean(self):
        data=super().clean()
        if data.get('action')=='replace' and not data.get('target'):self.add_error('target','Выберите существующую принятую причину.')
        return data


@login_required
@require_GET
@sqlite_serialized
def reviews(request):
    require_roles(request.user,service.REVIEWERS)
    service.sync_company(request.user.company)
    query=ActuationReason.objects.filter(company=request.user.company).select_related('created_by','reviewed_by','replacement')
    history=request.GET.get('history')=='1'
    query=query.exclude(status='PENDING') if history else query.filter(status='PENDING')
    from django.core.paginator import Paginator
    return render(request,'production/actuation_reason_reviews.html',{'page':Paginator(query.order_by('-created_at'),40).get_page(request.GET.get('page')),'history':history})


@login_required
@require_http_methods(['GET','POST'])
@sqlite_serialized
def review(request,pk):
    require_roles(request.user,service.REVIEWERS|service.EDITORS)
    reason=get_object_or_404(ActuationReason.objects.select_related('created_by','reviewed_by','replacement','task'),company=request.user.company,pk=pk)
    reviewer=role_code(request.user) in service.REVIEWERS
    if not reviewer and reason.created_by_id!=request.user.pk:raise PermissionDenied
    form=DecisionForm(request.POST if request.method=='POST' else None,reason=reason)
    if request.method=='POST':
        require_roles(request.user,service.REVIEWERS)
        if form.is_valid():
            try:
                service.decide(request.user,pk,form.cleaned_data['action'],form.cleaned_data['revision'],form.cleaned_data['target'].pk if form.cleaned_data.get('target') else None,form.cleaned_data['comment'])
                messages.success(request,'Решение сохранено. Связанные актировки обновлены.')
                return redirect('production:actuation_reason_review',pk=pk)
            except ValidationError as exc:form.add_error(None,exc)
    facts=DailyFact.objects.filter(company=request.user.company).exclude(actuation_type='').select_related('project_work__section__construction_object','work_item')
    usage=[fact for fact in facts if pk in fact.actuation_reason_ids]
    documents=ActuationDocument.objects.filter(company=request.user.company,fact_id__in=[f.pk for f in usage],removed_at=None)
    return render(request,'production/actuation_reason_review.html',{'reason':reason,'form':form,'reviewer':reviewer,'usage':usage,'documents':documents,'events':reason.task.events.all() if reason.task_id else []})


def document_json(record):
    return {'id':record.pk,'fact':record.fact_id,'name':record.original_name,'size':record.size,'url':reverse('production:actuation_document_download',args=[record.pk]),'remove_url':reverse('production:actuation_document_remove',args=[record.pk])}


def require_claims(user,obj,day,facts,session):
    try:session=UUID(str(session))
    except (ValueError,TypeError):raise ValidationError('Обновите форму и повторите загрузку.')
    claims={(c.work_id,c.work_item_id):c for c in WorkFactClaim.objects.filter(company=user.company,construction_object=obj,date=day,expires_at__gt=timezone.now())}
    for fact in facts:
        claim=claims.get((fact.project_work_id,fact.work_item_id))
        if not claim or claim.dispatcher_id!=user.pk or claim.session_key!=session:raise ValidationError('Возьмите резерв выбранной работы перед изменением актов. Данные другого диспетчера не изменены.')


@login_required
@require_http_methods(['GET','POST'])
@sqlite_serialized
def documents(request):
    stored=[]
    try:
        data=request.POST if request.method=='POST' else request.GET
        obj,day=day_scope(request,data,request.method=='POST')
        query=ActuationDocument.objects.filter(company=request.user.company,construction_object=obj,date=day,removed_at=None).select_related('uploaded_by')
        if request.method=='GET':return JsonResponse({'documents':[document_json(d) for d in query]})
        uploaded=request.FILES.getlist('files')
        if not uploaded or len(uploaded)>MAX_COUNT:raise ValidationError('Выберите от 1 до 30 файлов за одну загрузку.')
        if sum(f.size for f in uploaded)>MAX_TOTAL:raise ValidationError('Общий размер одной загрузки — до 100 МБ.')
        for file in uploaded:
            if file.size==0 or file.size>MAX_FILE:raise ValidationError('Каждый файл должен иметь размер от 1 байта до 25 МБ.')
            if Path(file.name).suffix.lower() not in EXTENSIONS:raise ValidationError('Допустимы PDF, изображения, документы Office и архивы.')
        try:
            batch=UUID(data.get('batch',''));ids=sorted(set(int(pk) for pk in data.getlist('facts')))
        except (ValueError,TypeError):raise ValidationError('Неверный список работ или номер загрузки.')
        if not ids or len(ids)>2000:raise ValidationError('Выберите работы с оформленной актировкой.')
        checksums=[]
        for file in uploaded:
            digest=sha256()
            for chunk in file.chunks():digest.update(chunk)
            checksums.append(digest.hexdigest())
        with transaction.atomic(),history_actor(request.user,'Прикрепление актов актировки'):
            type(obj).objects.filter(pk=obj.pk,company=obj.company).update(name=F('name'))
            facts=list(DailyFact.objects.select_for_update().filter(company=obj.company,date=day,project_work__section__construction_object=obj,pk__in=ids).exclude(actuation_type=''))
            if len(facts)!=len(ids):raise ValidationError('Актировка некоторых работ снята или день сброшен. Обновите форму.')
            require_claims(request.user,obj,day,facts,data.get('session'))
            shared={d.ordinal:d for d in ActuationDocument.objects.filter(company=obj.company,construction_object=obj,date=day,batch_token=batch)}
            for fact in facts:
                for index,file in enumerate(uploaded):
                    existing=ActuationDocument.objects.filter(company=obj.company,fact=fact,batch_token=batch,ordinal=index).first()
                    peer=existing or shared.get(index)
                    if peer and (peer.original_name!=Path(file.name).name[:255] or peer.size!=file.size or peer.checksum!=checksums[index]):raise ValidationError('Состав файлов изменился. Выберите файлы заново.')
                    if existing:continue
                    record=ActuationDocument(company=obj.company,fact=fact,construction_object=obj,date=day,original_name=Path(file.name).name[:255],size=file.size,checksum=checksums[index],uploaded_by=request.user,batch_token=batch,ordinal=index)
                    if peer:record.file.name=peer.file.name
                    else:
                        file.seek(0);record.file.save(file.name,file,save=False);stored.append((record.file.storage,record.file.name))
                    record.save();shared[index]=record
        return JsonResponse({'documents':[document_json(d) for d in query]})
    except ValidationError as exc:
        for storage,name in stored:storage.delete(name)
        return JsonResponse({'error':'; '.join(exc.messages)},status=400)
    except Exception:
        for storage,name in stored:storage.delete(name)
        raise


@login_required
@require_GET
def download(request,pk):
    require_roles(request.user,service.EDITORS|READ_ROLES)
    record=get_object_or_404(ActuationDocument,company=request.user.company,pk=pk,removed_at=None)
    if not scope_queryset(type(record.construction_object).objects.all(),request.user,production_read=True).filter(pk=record.construction_object_id).exists():raise Http404
    try:stream=record.file.open('rb')
    except FileNotFoundError:raise Http404('Файл отсутствует в хранилище.')
    response=FileResponse(stream,as_attachment=True,filename=record.original_name,content_type='application/octet-stream')
    response['X-Content-Type-Options']='nosniff';response['Cache-Control']='private, no-store'
    return response


@login_required
@require_POST
@sqlite_serialized
def remove(request,pk):
    try:
        obj,day=day_scope(request,request.POST,True)
        with transaction.atomic(),history_actor(request.user,'Удаление акта из текущей актировки'):
            type(obj).objects.filter(pk=obj.pk).update(name=F('name'))
            record=get_object_or_404(ActuationDocument.objects.select_for_update().select_related('fact'),company=obj.company,construction_object=obj,date=day,pk=pk,removed_at=None)
            if not record.fact:raise ValidationError('Работа уже удалена. Обновите форму.')
            require_claims(request.user,obj,day,[record.fact],request.POST.get('session'))
            record.removed_at=timezone.now();record.removed_by=request.user;record.save(update_fields=['removed_at','removed_by','updated_at'])
        return JsonResponse({'removed':pk})
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=400)
