from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q, Prefetch
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from core.permissions import require_roles, role_code
from .models import RecruitmentRequest, RecruitmentCandidate, ArrivalPreparation, CandidateDocument,TalentPoolCandidate
from . import recruitment as service
from .recruitment_forms import RequestForm, CandidateForm, ArrivalForm, RevisionForm, ReasonForm,TalentPoolForm,TalentPoolImportForm


def requests_for(user):
    return RecruitmentRequest.objects.filter(company=user.company).select_related('construction_object','assigned_to','created_by').prefetch_related(
        Prefetch('candidates',queryset=RecruitmentCandidate.objects.select_related('arrival__actual_stay').prefetch_related('documents')))


def context(user):
    role=role_code(user)
    return {'personnel_section':'recruitment','can_dispatch':role in service.DISPATCH,'can_recruit':role in service.RECRUIT}


def error(form,exc):form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились одновременно с вашим вводом. Обновите страницу.')


@login_required
@require_GET
def request_list(request):
    require_roles(request.user,service.READ)
    rows=requests_for(request.user)
    query=request.GET.get('q','').strip()
    if query:rows=rows.filter(Q(position__icontains=query)|Q(construction_object__name__icontains=query)|Q(candidates__full_name__icontains=query)).distinct()
    if request.GET.get('mine')=='1':rows=rows.filter(assigned_to=request.user)
    state=request.GET.get('state','active')
    if state=='cancelled':rows=rows.filter(cancelled_at__isnull=False)
    elif state!='all':rows=rows.filter(cancelled_at__isnull=True)
    page=Paginator(rows,30).get_page(request.GET.get('page'))
    for row in page:row.progress=service.progress(row)
    params=request.GET.copy();params.pop('page',None)
    return render(request,'personnel/recruitment_list.html',{**context(request.user),'page':page,'filter_query':query,'filter_state':state,'pagination_query':params.urlencode()})


@login_required
@require_GET
def pool_list(request):
    require_roles(request.user,service.READ)
    rows=TalentPoolCandidate.objects.filter(company=request.user.company).prefetch_related('vacancy_candidates__request')
    query=request.GET.get('q','').strip()
    if query:rows=rows.filter(Q(full_name__icontains=query)|Q(phone__icontains=query))
    return render(request,'personnel/recruitment_pool.html',{**context(request.user),'rows':rows,'query':query})


@login_required
@require_http_methods(['GET','POST'])
def pool_edit(request,pk=None):
    require_roles(request.user,service.RECRUIT)
    row=get_object_or_404(TalentPoolCandidate,company=request.user.company,pk=pk) if pk else None
    form=TalentPoolForm(request.POST if request.method=='POST' else None,company=request.user.company,user=request.user,instance=row)
    if request.method=='POST' and form.is_valid():
        saved=form.save();messages.success(request,'Кандидат сохранён в общем пуле.');return redirect('personnel:recruitment_pool')
    return render(request,'personnel/recruitment_form.html',{**context(request.user),'title':'Карточка кандидата общего пула','form':form})


@login_required
@require_http_methods(['GET','POST'])
def pool_import(request):
    require_roles(request.user,service.RECRUIT)
    form=TalentPoolImportForm(request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        from .talent_pool_import import import_sheet
        try:result=import_sheet(request.user,form.cleaned_data['file'])
        except ValidationError as exc:error(form,exc)
        else:
            messages.success(request,f"Лист «Магазин 2026» загружен: создано {result['created']}, обновлено {result['updated']}, пропущено {result['skipped']}.")
            return redirect('personnel:recruitment_pool')
    return render(request,'personnel/recruitment_form.html',{**context(request.user),'title':'Импорт магазина 2026','form':form})


@login_required
@require_http_methods(['GET','POST'])
def request_edit(request,pk=None):
    require_roles(request.user,service.DISPATCH)
    row=get_object_or_404(RecruitmentRequest,company=request.user.company,pk=pk) if pk else None
    initial={key:request.GET[key] for key in ('construction_object','position') if key in request.GET}
    form=RequestForm(request.POST if request.method=='POST' else None,company=request.user.company,instance=row,initial=initial)
    if request.method=='POST' and form.is_valid():
        try:saved=service.save_request(request.user,form.cleaned_data,pk,form.cleaned_data['revision'])
        except (ValidationError,IntegrityError) as exc:error(form,exc)
        else:
            messages.success(request,'Заявка сохранена.');return redirect('personnel:recruitment_detail',pk=saved.pk)
    return render(request,'personnel/recruitment_form.html',{**context(request.user),'title':'Изменить заявку' if pk else 'Новая заявка на подбор','form':form,'row':row})


@login_required
@require_http_methods(['GET','POST'])
def request_detail(request,pk):
    require_roles(request.user,service.READ)
    row=get_object_or_404(requests_for(request.user),pk=pk)
    form=RevisionForm(initial={'revision':row.revision});cancel_form=ReasonForm(initial={'revision':row.revision})
    if request.method=='POST':
        action=request.POST.get('action')
        require_roles(request.user,service.RECRUIT if action=='take' else service.DISPATCH)
        bound=RevisionForm(request.POST) if action=='take' else ReasonForm(request.POST)
        if bound.is_valid():
            try:
                if action=='take':service.take_request(request.user,pk,bound.cleaned_data['revision'])
                elif action=='cancel':service.cancel_request(request.user,pk,bound.cleaned_data['revision'],bound.cleaned_data['reason'])
                else:raise ValidationError('Неизвестное действие.')
            except (ValidationError,IntegrityError) as exc:error(bound,exc)
            else:return redirect('personnel:recruitment_detail',pk=pk)
        if action=='take':form=bound
        else:cancel_form=bound
    role=role_code(request.user)
    return render(request,'personnel/recruitment_detail.html',{**context(request.user),'row':row,'progress':service.progress(row),'form':form,'cancel_form':cancel_form,
        'can_manage_candidates':role in service.RECRUIT and (role!='RECRUITER' or row.assigned_to_id==request.user.pk),
        'events':row.events.select_related('actor','candidate')[:100]})


@login_required
@require_http_methods(['GET','POST'])
def candidate_edit(request,request_pk,pk=None):
    require_roles(request.user,service.RECRUIT)
    row=get_object_or_404(RecruitmentRequest,company=request.user.company,pk=request_pk)
    try:service.owned(request.user,row)
    except ValidationError as exc:raise PermissionDenied('; '.join(exc.messages))
    c=get_object_or_404(RecruitmentCandidate,company=request.user.company,request=row,pk=pk) if pk else None
    form=CandidateForm(request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None,company=request.user.company,request_row=row,instance=c)
    if request.method=='POST' and form.is_valid():
        try:service.save_candidate(request.user,row.pk,form.cleaned_data,pk,form.cleaned_data['revision'],files=form.cleaned_data['documents'])
        except (ValidationError,IntegrityError) as exc:error(form,exc)
        else:
            messages.success(request,'Кандидат сохранён.');return redirect('personnel:recruitment_detail',pk=row.pk)
    return render(request,'personnel/recruitment_form.html',{**context(request.user),'title':'Изменить кандидата' if pk else 'Новый кандидат','form':form,'row':row})


@login_required
@require_POST
def candidate_handoff(request,pk):
    require_roles(request.user,service.RECRUIT|service.DISPATCH)
    c=get_object_or_404(RecruitmentCandidate,company=request.user.company,pk=pk)
    form=RevisionForm(request.POST)
    if form.is_valid():
        try:prep=service.handoff(request.user,pk,form.cleaned_data['revision'])
        except (ValidationError,IntegrityError) as exc:messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились. Обновите страницу.')
        else:
            messages.success(request,'Создана подготовка заезда. Заполните ВЖГ и плановую дату, затем отправьте заявку на согласование.')
            if role_code(request.user) in service.DISPATCH:return redirect('personnel:recruitment_arrival',pk=prep.pk)
    else:messages.error(request,'Форма передачи устарела. Обновите страницу.')
    return redirect('personnel:recruitment_detail',pk=c.request_id)


@login_required
@require_GET
def document_download(request,pk):
    require_roles(request.user,service.READ)
    doc=get_object_or_404(CandidateDocument,company=request.user.company,candidate__company=request.user.company,pk=pk)
    try:stream=doc.file.open('rb')
    except FileNotFoundError:raise Http404('Файл не найден в хранилище.')
    response=FileResponse(stream,as_attachment=True,filename=doc.original_name,content_type='application/octet-stream')
    response['X-Content-Type-Options']='nosniff';response['Cache-Control']='private, no-store'
    return response


@login_required
@require_http_methods(['GET','POST'])
def arrival_edit(request,pk):
    require_roles(request.user,service.DISPATCH)
    prep=get_object_or_404(ArrivalPreparation.objects.select_related('candidate__request__construction_object','actual_stay','employee','camp'),company=request.user.company,pk=pk)
    form=ArrivalForm(request.POST if request.method=='POST' and request.POST.get('action')!='cancel' else None,company=request.user.company,preparation=prep)
    cancel_form=ReasonForm(request.POST if request.method=='POST' and request.POST.get('action')=='cancel' else None,initial={'revision':prep.revision})
    if request.method=='POST':
        action=request.POST.get('action');bound=cancel_form if action=='cancel' else form
        if bound.is_valid():
            try:
                if action=='cancel':service.cancel_preparation(request.user,pk,bound.cleaned_data['revision'],bound.cleaned_data['reason'])
                elif action in {'save','confirm','submit'}:service.prepare_arrival(request.user,pk,bound.cleaned_data['revision'],bound.cleaned_data,confirm=action=='confirm',workflow=action=='submit')
                else:raise ValidationError('Неизвестное действие.')
            except (ValidationError,IntegrityError) as exc:error(bound,exc)
            else:
                messages.success(request,'Заезд отправлен руководителю на согласование.' if action=='submit' else 'Заезд подтверждён.' if action=='confirm' else 'Подготовка заезда сохранена.')
                if action=='submit':return redirect('personnel:my_movement_requests')
                if action=='confirm':return redirect('personnel:movements')
                return redirect('personnel:recruitment_arrival',pk=pk)
    return render(request,'personnel/recruitment_arrival.html',{**context(request.user),'personnel_section':'movements','prep':prep,'form':form,'cancel_form':cancel_form})
