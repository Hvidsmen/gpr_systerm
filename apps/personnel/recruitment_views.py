from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError, PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError,transaction
from django.db.models import Count,Q, Prefetch
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST, require_GET
from core.permissions import require_roles, role_code
from apps.projects.models import ConstructionObject
from apps.resources.models import Position
from .models import (RecruitmentRequest,RecruitmentCandidate,RecruitmentSpecialist,ArrivalPreparation,CandidateDocument,
    TalentPoolCandidate,TalentPoolStageEvent,MovementReviewRequest,Camp)
from . import recruitment as service
from .recruitment_forms import (RequestForm,CandidateForm,ArrivalForm,RevisionForm,ReasonForm,TalentPoolForm,
    TalentPoolImportForm,BulkArrivalCommonForm,BulkArrivalRowForm,RecruiterAssignmentForm,RecruitmentSpecialistForm)
from .recruitment_models import POOL_UNAVAILABLE_STAGES, RECRUITMENT_TERMINAL_STAGES


FUNNEL_GROUPS=[
    ('work','В работе',{'NEW','SCREENING','HR_INTERVIEW','MANAGER_APPROVAL','SECURITY_CHECK','MEDICAL_DOCUMENTS'}),
    ('selected','Отобраны',{'SELECTED','READY','RESERVE'}),
    ('transferred','Поставлены на заезд',{'TRANSFERRED'}),
    ('approved','Не заехали',{'ARRIVAL_APPROVED'}),
    ('arrived','Заехали',{'ARRIVED'}),
    ('rejected','Отказники',RECRUITMENT_TERMINAL_STAGES),
]


def _position_name(value):return ' '.join((value or '').split())


def _position_funnel_summary(candidate_rows,request_rows,application_rows):
    """Compare the vacancy plan with one current funnel card per person."""
    result={};canonical_by_name={};latest_application={};stage_labels=dict(TalentPoolCandidate._meta.get_field('stage').choices)
    def ensure(key,label):
        if key not in result:
            result[key]={'position':label or 'Должность не указана','request_count':0,'required':0,'candidate_count':0,
                'counts':{value:0 for value,_,_ in FUNNEL_GROUPS},'people':[]}
        return result[key]
    for row in request_rows:
        label=_position_name(row['position_ref__name'] or row['position'])
        key=('position',row['position_ref_id']) if row['position_ref_id'] else ('name',label.casefold())
        if label:canonical_by_name.setdefault(label.casefold(),key)
        item=ensure(key,label);item['request_count']+=1;item['required']+=row['quantity']
    for row in application_rows:latest_application.setdefault(row['pool_candidate_id'],row)
    group_positions={value:set() for value,_,_ in FUNNEL_GROUPS};all_positions=set()
    for row in candidate_rows:
        application=latest_application.get(row['pk'])
        if application:
            position_id=application['request__position_ref_id'];label=_position_name(application['request__position_ref__name'] or application['request__position'])
        else:
            position_id=row['position_id'];label=_position_name(row['position__name'] or (row['source_data'] or {}).get('position',''))
        key=('position',position_id) if position_id else canonical_by_name.get(label.casefold(),('name',label.casefold()))
        item=ensure(key,label);item['candidate_count']+=1
        item['people'].append({'pk':row['pk'],'full_name':row['full_name'],'stage':row['stage'],'stage_label':stage_labels[row['stage']],
            'construction_object':(application['request__construction_object__name'] if application else
                row['construction_object__name'] or (row['source_data'] or {}).get('construction_site','')),
            'request_id':application['request_id'] if application else None,
            'request_position':application['request__position'] if application else '',
            'request_object':application['request__construction_object__name'] if application else ''})
        if label:all_positions.add(key)
        for value,_,stages in FUNNEL_GROUPS:
            if row['stage'] in stages:
                item['counts'][value]+=1
                if label:group_positions[value].add(key)
                break
    rows=[]
    for item in result.values():
        item['groups']=[{'value':value,'count':item['counts'][value]} for value,_,_ in FUNNEL_GROUPS]
        item['people'].sort(key=lambda person:person['full_name'].casefold())
        rows.append(item)
    rows.sort(key=lambda item:(item['position']=='Должность не указана',item['position'].casefold()))
    return rows,len(all_positions),{value:len(keys) for value,keys in group_positions.items()}


def requests_for(user):
    return RecruitmentRequest.objects.filter(company=user.company).select_related('construction_object','assigned_to','created_by').prefetch_related(
        Prefetch('candidates',queryset=RecruitmentCandidate.objects.select_related('arrival__actual_stay').prefetch_related('documents')))


def context(user):
    role=role_code(user)
    return {'personnel_section':'recruitment','can_dispatch':role in service.DISPATCH,'can_recruit':role in service.RECRUIT}


def error(form,exc):form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились одновременно с вашим вводом. Обновите страницу.')


@login_required
@require_GET
def specialist_list(request):
    require_roles(request.user,service.READ)
    rows=RecruitmentSpecialist.objects.filter(company=request.user.company).select_related('user').annotate(
        candidate_count=Count('candidates',distinct=True))
    query=request.GET.get('q','').strip()
    if query:
        rows=rows.filter(Q(name__icontains=query)|Q(user__username__icontains=query)|
            Q(user__first_name__icontains=query)|Q(user__last_name__icontains=query))
    state=request.GET.get('state','all')
    if state=='active':rows=rows.filter(is_active=True)
    elif state=='inactive':rows=rows.filter(is_active=False)
    return render(request,'personnel/recruitment_specialists.html',{**context(request.user),'rows':rows,
        'query':query,'state':state,'can_manage_specialists':role_code(request.user) in service.ASSIGN})


@login_required
@require_http_methods(['GET','POST'])
def specialist_edit(request,pk=None):
    require_roles(request.user,service.ASSIGN)
    queryset=RecruitmentSpecialist.objects.filter(company=request.user.company)
    row=get_object_or_404(queryset,pk=pk) if pk else RecruitmentSpecialist(company=request.user.company)
    form=RecruitmentSpecialistForm(request.POST if request.method=='POST' else None,company=request.user.company,instance=row)
    if request.method=='POST' and form.is_valid():
        try:form.save()
        except IntegrityError:form.add_error('name','Такой рекрутер уже есть в справочнике.')
        else:
            messages.success(request,'Рекрутер сохранён в справочнике.')
            return redirect('personnel:recruitment_specialists')
    return render(request,'personnel/recruitment_specialist_form.html',{**context(request.user),'form':form,'row':row})


@login_required
@require_GET
def request_list(request,recruitment_page='bank'):
    require_roles(request.user,service.READ)
    recruitment_page='requests' if recruitment_page=='requests' else 'bank'
    def selected_ids(name):
        return sorted({int(value) for value in request.GET.getlist(name) if value.isdigit()})
    selected_position_ids=selected_ids('position');selected_camp_ids=selected_ids('camp')
    selected_object_ids=selected_ids('project')
    rows=requests_for(request.user)
    query=request.GET.get('q','').strip()
    if query:rows=rows.filter(Q(position__icontains=query)|Q(construction_object__name__icontains=query)|Q(candidates__full_name__icontains=query)).distinct()
    if selected_position_ids:
        rows=rows.filter(Q(position_ref_id__in=selected_position_ids)|Q(candidates__pool_candidate__position_id__in=selected_position_ids)).distinct()
    if selected_object_ids:
        rows=rows.filter(Q(construction_object_id__in=selected_object_ids)|Q(candidates__pool_candidate__construction_object_id__in=selected_object_ids)).distinct()
    if selected_camp_ids:rows=rows.filter(candidates__pool_candidate__camp_id__in=selected_camp_ids).distinct()
    if request.GET.get('mine')=='1':rows=rows.filter(assigned_to=request.user)
    state=request.GET.get('state','active')
    if state=='cancelled':rows=rows.filter(cancelled_at__isnull=False)
    elif state=='completed':rows=rows.filter(completed_at__isnull=False)
    elif state!='all':rows=rows.filter(cancelled_at__isnull=True,completed_at__isnull=True)
    plan_rows=list(rows.values('position_ref_id','position_ref__name','position','quantity'))
    page=Paginator(rows,30).get_page(request.GET.get('page'))
    for row in page:row.progress=service.progress(row)
    applications=RecruitmentCandidate.objects.select_related('request__construction_object','arrival__movement_request').order_by('-created_at','-pk')
    candidates=TalentPoolCandidate.objects.filter(company=request.user.company).select_related(
        'position','construction_object','camp','base_city','responsible_hr').prefetch_related(
        Prefetch('vacancy_candidates',queryset=applications))
    if query:candidates=candidates.filter(Q(full_name__icontains=query)|Q(phone__icontains=query)|
        Q(vacancy_candidates__request__position__icontains=query)|Q(vacancy_candidates__request__construction_object__name__icontains=query)).distinct()
    if selected_position_ids:
        candidates=candidates.filter(Q(position_id__in=selected_position_ids)|
            Q(vacancy_candidates__request__position_ref_id__in=selected_position_ids)).distinct()
    if selected_object_ids:
        candidates=candidates.filter(Q(construction_object_id__in=selected_object_ids)|
            Q(vacancy_candidates__request__construction_object_id__in=selected_object_ids)).distinct()
    if selected_camp_ids:candidates=candidates.filter(camp_id__in=selected_camp_ids)
    candidate_rows=list(candidates.values('pk','full_name','stage','position_id','position__name','construction_object__name','source_data'))
    candidate_ids=[row['pk'] for row in candidate_rows]
    application_rows=list(RecruitmentCandidate.objects.filter(company=request.user.company,pool_candidate_id__in=candidate_ids).order_by(
        'pool_candidate_id','-created_at','-pk').values('pool_candidate_id','request_id','request__position_ref_id','request__position_ref__name',
            'request__position','request__construction_object__name'))
    position_summary,candidate_position_count,group_position_counts=_position_funnel_summary(candidate_rows,plan_rows,application_rows)
    candidate_total=len(candidate_rows)
    selected_group=request.GET.get('candidate_stage','')
    selected_stages=next((stages for value,_,stages in FUNNEL_GROUPS if value==selected_group),None)
    opened_people=False
    for position in position_summary:
        position['visible_people']=[person for person in position['people'] if selected_stages is None or person['stage'] in selected_stages]
        position['visible_count']=len(position['visible_people'])
        position['open_people']=bool(position['visible_people']) and not opened_people
        if position['open_people']:opened_people=True
    if selected_stages is not None:position_summary=[position for position in position_summary if position['visible_people']]
    groups=[]
    for value,label,stages in FUNNEL_GROUPS:
        groups.append({'value':value,'label':label,'count':sum(row['stage'] in stages for row in candidate_rows),
            'position_count':group_position_counts[value],'active':selected_group==value})
    if selected_stages is not None:candidates=candidates.filter(stage__in=selected_stages)
    candidate_page=Paginator(candidates.order_by('-updated_at','full_name','pk'),30).get_page(request.GET.get('candidate_page'))
    params=request.GET.copy();params.pop('page',None)
    funnel_params=request.GET.copy();funnel_params.pop('candidate_stage',None);funnel_params.pop('candidate_page',None);funnel_params.pop('page',None)
    position_options=Position.objects.filter(company=request.user.company).order_by('name','pk')
    camp_options=Camp.objects.filter(company=request.user.company).order_by('name','pk')
    object_rows=list(ConstructionObject.objects.filter(company=request.user.company).select_related('project','parent').order_by('project__name','name','pk'))
    objects_by_id={row.pk:row for row in object_rows};object_options=[]
    for row in object_rows:
        names=[row.name];parent_id=row.parent_id;seen={row.pk}
        while parent_id and parent_id not in seen and parent_id in objects_by_id:
            seen.add(parent_id);parent=objects_by_id[parent_id];names.append(parent.name);parent_id=parent.parent_id
        object_options.append({'pk':row.pk,'label':f'{row.project.name} → {" → ".join(reversed(names))}'})
    return render(request,'personnel/recruitment_list.html',{**context(request.user),'page':page,'filter_query':query,'filter_state':state,
        'recruitment_page':recruitment_page,
        'pagination_query':params.urlencode(),'candidate_page':candidate_page,'funnel_groups':groups,'candidate_stage':selected_group,
        'candidate_total':candidate_total,'candidate_position_count':candidate_position_count,'position_summary':position_summary,
        'candidate_stage_choices':TalentPoolCandidate._meta.get_field('stage').choices,'position_options':position_options,
        'camp_options':camp_options,'object_options':object_options,'selected_position_ids':selected_position_ids,
        'selected_camp_ids':selected_camp_ids,'selected_object_ids':selected_object_ids,'funnel_query':funnel_params.urlencode()})


@login_required
@require_GET
def pool_list(request):
    require_roles(request.user,service.READ)
    applications=RecruitmentCandidate.objects.select_related('request__construction_object','arrival__movement_request').order_by('-created_at','-pk')
    rows=TalentPoolCandidate.objects.filter(company=request.user.company).select_related(
        'position','construction_object','camp','base_city','responsible_hr').prefetch_related(
            Prefetch('vacancy_candidates',queryset=applications))
    query=request.GET.get('q','').strip()
    if query:rows=rows.filter(Q(full_name__icontains=query)|Q(phone__icontains=query))
    stage=request.GET.get('stage','')
    if stage in dict(TalentPoolCandidate._meta.get_field('stage').choices):rows=rows.filter(stage=stage)
    return render(request,'personnel/recruitment_pool.html',{**context(request.user),'rows':rows,'query':query,'stage':stage,
        'stage_choices':TalentPoolCandidate._meta.get_field('stage').choices})


@login_required
@require_GET
def candidate_card(request,pk):
    require_roles(request.user,service.READ)
    applications=RecruitmentCandidate.objects.select_related(
        'request__construction_object','request__assigned_to','arrival__movement_request','arrival__actual_stay').prefetch_related('documents').order_by('-created_at','-pk')
    row=get_object_or_404(TalentPoolCandidate.objects.filter(company=request.user.company).select_related(
        'position','construction_object','camp','base_city','responsible_hr').prefetch_related(
        Prefetch('vacancy_candidates',queryset=applications)),pk=pk)
    from .models import RecruitmentEvent
    events=RecruitmentEvent.objects.filter(company=request.user.company,candidate__pool_candidate=row).select_related(
        'request__construction_object','candidate','actor').order_by('-created_at','-pk')[:200]
    source_events=(row.source_data or {}).get('funnel_sources',[])
    source_applications=[event for event in source_events if event.get('sheet')=='План найма']
    stage_events=row.stage_events.select_related('actor')[:200]
    return render(request,'personnel/recruitment_candidate_card.html',{**context(request.user),'row':row,'events':events,
        'source_events':source_events,'source_applications':source_applications,'stage_events':stage_events,
        'has_applications':bool(row.vacancy_candidates.all())})


@login_required
@require_http_methods(['GET','POST'])
def candidate_inline(request,pk):
    require_roles(request.user,service.READ)
    row=get_object_or_404(TalentPoolCandidate,company=request.user.company,pk=pk)
    can_edit=role_code(request.user) in service.RECRUIT
    if request.method=='POST':require_roles(request.user,service.RECRUIT)
    previous_stage=row.stage
    form=TalentPoolForm(request.POST if request.method=='POST' else None,company=request.user.company,user=request.user,
        instance=row,auto_id=f'id_candidate_{row.pk}_%s')
    saved=False
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            row=form.save()
            if previous_stage!=row.stage:
                TalentPoolStageEvent.objects.create(company=request.user.company,candidate=row,from_stage=previous_stage,
                    to_stage=row.stage,actor=request.user,note=form.cleaned_data.get('stage_reason','').strip())
        saved=True;form=TalentPoolForm(company=request.user.company,user=request.user,instance=row,
            auto_id=f'id_candidate_{row.pk}_%s')
    if not can_edit:
        for field in form.fields.values():field.disabled=True
    status=422 if request.method=='POST' and not saved else 200
    return render(request,'personnel/_recruitment_candidate_inline.html',{
        'row':row,'form':form,'can_edit':can_edit,'saved':saved},status=status)


@login_required
@require_POST
def candidate_stage_update(request,pk):
    require_roles(request.user,service.RECRUIT)
    choices=dict(TalentPoolCandidate._meta.get_field('stage').choices)
    stage=request.POST.get('stage','')
    if stage not in choices:return JsonResponse({'error':'Выберите этап воронки.'},status=400)
    note=request.POST.get('note','').strip()
    with transaction.atomic():
        row=get_object_or_404(TalentPoolCandidate.objects.select_for_update(),company=request.user.company,pk=pk)
        previous=row.stage
        if previous!=stage:
            row.stage=stage;row.is_active=stage not in POOL_UNAVAILABLE_STAGES
            row.save(update_fields=['stage','is_active','updated_at'])
            TalentPoolStageEvent.objects.create(company=request.user.company,candidate=row,from_stage=previous,
                to_stage=stage,actor=request.user,note=note)
    return JsonResponse({'id':row.pk,'stage':row.stage,'stage_label':row.get_stage_display()})


@login_required
@require_http_methods(['GET','POST'])
def pool_edit(request,pk=None):
    require_roles(request.user,service.RECRUIT)
    row=get_object_or_404(TalentPoolCandidate,company=request.user.company,pk=pk) if pk else None
    previous_stage=row.stage if row else None
    form=TalentPoolForm(request.POST if request.method=='POST' else None,company=request.user.company,user=request.user,instance=row)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            saved=form.save()
            if previous_stage and previous_stage!=saved.stage:
                TalentPoolStageEvent.objects.create(company=request.user.company,candidate=saved,from_stage=previous_stage,
                    to_stage=saved.stage,actor=request.user,note=form.cleaned_data.get('stage_reason','').strip())
        messages.success(request,'Кандидат сохранён в общем пуле.');return redirect('personnel:recruitment_pool')
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
            messages.success(request,f"Воронка загружена: создано карточек {result['created']}, обновлено {result['updated']}, без изменений {result['skipped']}.")
            return redirect('personnel:recruitment_pool')
    return render(request,'personnel/recruitment_form.html',{**context(request.user),'title':'Импорт воронки подбора','form':form})


@login_required
@require_http_methods(['GET','POST'])
def request_edit(request,pk=None):
    require_roles(request.user,service.DISPATCH)
    row=get_object_or_404(RecruitmentRequest,company=request.user.company,pk=pk) if pk else None
    initial={key:request.GET[key] for key in ('construction_object',) if key in request.GET}
    if request.GET.get('position'):
        from apps.resources.models import Position
        matches=list(Position.objects.filter(company=request.user.company,name__iexact=request.GET['position'].strip())[:2])
        if len(matches)==1:initial['position_ref']=matches[0].pk
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
    assignment_form=RecruiterAssignmentForm(initial={'revision':row.revision},company=request.user.company,current=row.assigned_to)
    cancel_form=ReasonForm(initial={'revision':row.revision})
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='assign':
            require_roles(request.user,service.ASSIGN)
            bound=RecruiterAssignmentForm(request.POST,company=request.user.company,current=row.assigned_to)
        elif action=='cancel':
            require_roles(request.user,service.DISPATCH)
            bound=ReasonForm(request.POST)
        else:
            raise PermissionDenied('Неизвестное действие.')
        if bound.is_valid():
            try:
                if action=='assign':service.assign_request(request.user,pk,bound.cleaned_data['recruiter'],bound.cleaned_data['revision'])
                else:service.cancel_request(request.user,pk,bound.cleaned_data['revision'],bound.cleaned_data['reason'])
            except (ValidationError,IntegrityError) as exc:error(bound,exc)
            else:return redirect('personnel:recruitment_detail',pk=pk)
        if action=='assign':assignment_form=bound
        else:cancel_form=bound
    role=role_code(request.user)
    return render(request,'personnel/recruitment_detail.html',{**context(request.user),'can_dispatch':role in service.DISPATCH and not row.completed_at,
        'row':row,'progress':service.progress(row),'assignment_form':assignment_form,'cancel_form':cancel_form,
        'can_assign_recruiter':role in service.ASSIGN and not row.completed_at and not row.cancelled_at,
        'can_manage_candidates':not row.completed_at and (role=='ADMIN' or role=='RECRUITER' and row.assigned_to_id==request.user.pk),
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
@require_POST
def bulk_arrival_handoff(request):
    require_roles(request.user,service.DISPATCH)
    try:
        batch,preparations=service.bulk_prepare_arrivals(request.user,request.POST.getlist('selected'))
    except (ValidationError,IntegrityError) as exc:
        messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились. Обновите страницу.')
        return redirect('personnel:new_employee_arrivals')
    messages.success(request,f'Создан черновик пакетной заявки: {len(preparations)} сотрудников. Заполните обязательные данные.')
    return redirect('personnel:recruitment_arrival_bulk_edit',batch=batch)


@login_required
@require_http_methods(['GET','POST'])
def bulk_arrival_edit(request,batch):
    require_roles(request.user,service.DISPATCH)
    rows=list(MovementReviewRequest.objects.filter(company=request.user.company,requested_by=request.user,
        approval_batch=batch,kind='ARRIVAL').select_related('person','construction_object','to_camp').order_by('person__full_name','pk'))
    if not rows:raise Http404('Черновик пакетной заявки не найден.')
    submitted=any(row.status!='OPEN' for row in rows)
    action=request.POST.get('action') if request.method=='POST' else ''
    if request.method=='POST' and action=='cancel' and not submitted:
        try:service.cancel_bulk_arrival_draft(request.user,batch,request.POST.get('reason',''))
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        else:
            messages.success(request,'Пакетный черновик отменён. Сотрудники возвращены в рекомендации.')
            return redirect('personnel:new_employee_arrivals')
    confirm=action=='submit'
    initial_object=rows[0].construction_object_id if len({row.construction_object_id for row in rows})==1 else None
    initial_camp=rows[0].to_camp_id if len({row.to_camp_id for row in rows})==1 else None
    common=BulkArrivalCommonForm(request.POST if request.method=='POST' else None,company=request.user.company,required=confirm,
        initial={'construction_object':initial_object,'camp':initial_camp})
    row_forms=[]
    for row in rows:
        initial={'planning_position_name':row.planning_position_name,'position_name':row.position_name,'base_city':row.base_city,
            'phone':row.phone,'movement_date':row.movement_date,'planned_hub_departure_date':row.planned_hub_departure_date,'note':row.note}
        form=BulkArrivalRowForm(request.POST if request.method=='POST' else None,prefix=f'row-{row.pk}',required=confirm,initial=initial)
        if submitted:
            for field in form.fields.values():field.disabled=True
        row_forms.append((row,form))
    if submitted:
        for field in common.fields.values():field.disabled=True
    if request.method=='POST' and action!='cancel' and not submitted:
        valid=common.is_valid() and all(form.is_valid() for _,form in row_forms)
        if valid:
            try:service.save_bulk_arrival_draft(request.user,batch,common.cleaned_data['construction_object'],common.cleaned_data['camp'],
                {row.pk:form.cleaned_data for row,form in row_forms},submit=confirm)
            except (ValidationError,IntegrityError) as exc:messages.error(request,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Данные изменились. Обновите страницу.')
            else:
                messages.success(request,'Пакетная заявка отправлена на согласование.' if confirm else 'Черновик сохранён.')
                if confirm:return redirect('personnel:my_movement_requests')
                return redirect('personnel:recruitment_arrival_bulk_edit',batch=batch)
    from apps.projects.models import ConstructionObject
    from .models import CampObjectLink
    objects=list(ConstructionObject.objects.filter(company=request.user.company).values_list('pk','parent_id'))
    parents=dict(objects);project_camps={str(pk):set() for pk,_ in objects}
    for object_id,camp_id in CampObjectLink.objects.filter(company=request.user.company,camp__is_active=True).values_list('construction_object_id','camp_id'):
        current=object_id;seen=set()
        while current and current not in seen:
            seen.add(current);project_camps.setdefault(str(current),set()).add(camp_id);current=parents.get(current)
    from apps.planning.models import WorkTask
    approval_task=WorkTask.objects.filter(company=request.user.company,key=f'personnel-movement-batch:{batch}').first()
    return render(request,'personnel/recruitment_arrival_bulk.html',{**context(request.user),'personnel_section':'movements',
        'batch':batch,'rows':rows,'common_form':common,'row_forms':row_forms,'submitted':submitted,
        'project_camps':{key:sorted(value) for key,value in project_camps.items()},'approval_task':approval_task})


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
