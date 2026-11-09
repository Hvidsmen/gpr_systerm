from calendar import monthrange
from datetime import date,timedelta
from io import BytesIO
from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from core.fact_edit_window import can_edit_fact_day,require_fact_day,window_context
from django.db import transaction,IntegrityError
from django.db.models import Q
from django.http import HttpResponse,Http404
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .views import access,scoped
from .models import Camp,CampObjectLink,CampStay,HousingEvent,MovementReviewRequest,Person,Board,StaffingGroup,StaffingSlot
from .forms import CampForm,MovementForm,MovementRecommendationForm,MovementReviewForm,PersonScheduleForm,ZupScheduleChangeForm
from . import housing
from .services import calendar_for,departure_days


def selected_day(request):
    try:return forms.DateField().clean((request.POST if request.method=='POST' else request.GET).get('date') or timezone.localdate().isoformat())
    except ValidationError:return timezone.localdate()


def camp_url(camp):return reverse('personnel:housing')+f'?camp={camp.pk}'


def movement_access(request):
    from core.permissions import require_roles
    require_roles(request.user,{'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'})


@login_required
@require_http_methods(['GET','POST'])
def remove_from_dispatch(request,pk):
    movement_access(request)
    person=get_object_or_404(Person.objects.select_related('employee'),pk=pk,company=request.user.company,dispatcher_removed_at=None)
    active=person.assignments.filter(planning_position__isnull=False,cancelled_at=None,replaced_on=None).exists()
    pending=person.movement_review_requests.filter(status='OPEN').exists()
    if request.method=='POST':
        reason=' '.join(request.POST.get('reason','').split())
        if active:messages.error(request,'Сначала удалите действующее назначение сотрудника.')
        elif pending:messages.error(request,'Сначала завершите или отмените открытую заявку перевахты.')
        elif not reason:messages.error(request,'Укажите причину удаления из диспетчерской.')
        else:
            person.dispatcher_removed_at=timezone.now();person.dispatcher_removed_by=request.user;person.dispatcher_removal_reason=reason
            person.save(update_fields=['dispatcher_removed_at','dispatcher_removed_by','dispatcher_removal_reason','updated_at'])
            messages.success(request,f'{person.display_name} удалён из диспетчерской. Кадровая карточка и история сохранены.')
            return redirect(reverse('personnel:housing'))
    return render(request,'personnel/remove_from_dispatch.html',{'person':person,'has_active_assignment':active,'has_pending_request':pending,'personnel_section':'management'})


@login_required
def camps(request):
    from core.permissions import require_roles,READ_ROLES
    require_roles(request.user,READ_ROLES|{'HR_HEAD','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER'})
    qs=Camp.objects.filter(company=request.user.company).prefetch_related('objects_served')
    query=request.GET.get('q','').strip()
    if query:qs=qs.filter(Q(name__icontains=query)|Q(address__icontains=query)|Q(objects_served__name__icontains=query)).distinct()
    if request.GET.get('export')=='xlsx':
        return workbook_response('camps.xlsx','ВЖГ',[['ВЖГ','Расположение','Действующий','Связанные объекты'],*[[c.name,c.address,'Да' if c.is_active else 'Нет','; '.join(o.name for o in c.objects_served.all())] for c in qs]])
    return render(request,'personnel/camps.html',{'camps':qs,'query':query})


@login_required
@require_http_methods(['GET','POST'])
def camp_edit(request,pk=None):
    access(request)
    obj=get_object_or_404(scoped(request,Camp),pk=pk) if pk else Camp(company=request.user.company)
    form=CampForm(request.POST if request.method=='POST' else None,company=request.user.company,instance=obj)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                from apps.accounts.models import Company
                from django.db.models import F
                Company.objects.filter(pk=request.user.company_id).update(name=F('name'))
                # Match production's object-before-camp lock order, including FK checks.
                from apps.projects.models import ConstructionObject
                object_ids={row.pk for row in form.cleaned_data['objects_served']}
                if obj.pk:object_ids.update(obj.objects_served.values_list('pk',flat=True))
                list(ConstructionObject.objects.select_for_update().filter(company=request.user.company,pk__in=object_ids).order_by('pk'))
                if obj.pk:
                    current=Camp.objects.select_for_update().get(pk=obj.pk)
                    if current.updated_at.isoformat()!=form.cleaned_data['version']:raise ValidationError('ВЖГ или связи уже изменены другим пользователем. Обновите форму.')
                saved=form.save();selected={o.pk for o in form.cleaned_data['objects_served']}
                saved.object_links.exclude(construction_object_id__in=selected).delete()
                for object_id in selected:
                    CampObjectLink.objects.get_or_create(company=request.user.company,camp=saved,construction_object_id=object_id)
        except IntegrityError:form.add_error('name','Такой ВЖГ уже существует. Обновите справочник.')
        except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
        else:
            messages.success(request,'ВЖГ и связи с объектами сохранены.');return redirect('personnel:camps')
    return render(request,'personnel/form.html',{'form':form,'title':'Изменить ВЖГ' if pk else 'Новый ВЖГ'})


def roster_rows(company,camp,day,query='',position=''):
    stays=housing.present_stays(company,day).filter(camp=camp).select_related('person__employee__position__planning_mapping__brigade__group','person__schedule_slot__pattern','person__schedule_slot__group__board','recorded_by')
    if query:stays=stays.filter(Q(person__full_name__icontains=query)|Q(person__employee_number__icontains=query)|Q(person__employee__employee_number__icontains=query)|Q(person__employee__zup_full_name__icontains=query)|Q(person__employee__position__name__icontains=query))
    if position:stays=stays.filter(person__employee__position_id=position)
    days=[day.replace(day=n) for n in range(1,monthrange(day.year,day.month)[1]+1)]
    rows=[]
    for stay in stays.order_by('person__employee__position__name','person__full_name','pk'):
        person=stay.person;slot=housing.schedule_slot(person);calendar=calendar_for(slot,person=person,start=day.replace(day=1),end=day+timedelta(days=730))
        planned_departure=next(iter(departure_days(calendar,stay.arrived_on)),'')
        departure_date=date.fromisoformat(planned_departure) if planned_departure else None
        until_departure=(departure_date-day).days if departure_date else None
        rows.append({'stay':stay,'person':person,'position':person.employee.position if person.employee_id else None,'slot':slot,
            'codes':[calendar.get(d.isoformat(),'') for d in days],'today_code':calendar.get(day.isoformat(),''),
            'planned_departure':planned_departure,'planned_departure_display':departure_date,'departure_days':until_departure,
            'departure_warning':('overdue' if until_departure<0 else 'soon' if until_departure<=14 else '') if until_departure is not None else '',
            'position_label':str(person.employee.position) if person.employee_id and person.employee.position_id else slot.group.position if slot else 'Должность не указана'})
    from apps.resources.group_order import position_order
    rows.sort(key=lambda row:(position_order(row['position'],row['position_label']),row['person'].full_name.casefold()))
    return rows,days


@login_required
@require_http_methods(['GET','POST'])
def workspace(request, movements=False, movement_page='new'):
    movements = movements or request.method == 'POST' or bool(request.GET.get('employee') or request.GET.get('person') or request.GET.get('action'))
    movement_access(request) if movements else access(request)
    day=selected_day(request) if movements else timezone.localdate()
    template = 'personnel/housing_movements.html' if movements else 'personnel/housing.html'
    extra = {**movement_context(request),'dispatcher_page':movement_page,'movement_page':movement_page} if movements else {'personnel_section':'management','personnel_today_only':True}
    from .rotation import RotationFilter,normalize_rotation_filter_data,rotation_project_camps,rotation_rows
    extra['rotation_project_camps']=rotation_project_camps(request.user.company)
    raw=(request.GET if request.method=='GET' else request.POST).copy()
    raw['date']=str(day)
    raw=normalize_rotation_filter_data(raw,request.user.company)
    selector=RotationFilter(raw,company=request.user.company)
    if not selector.is_valid():
        if raw.get('camp'):raise Http404('ВЖГ не найден.')
        return render(request,template,{**extra,'rotation_filter':selector,'day':day},status=400)
    selected_camps=selector.selected_camps(request.user.company)
    camps=(Camp.objects.filter(company=request.user.company) if movements else scoped(request,Camp)).order_by('name')
    camp_id=(request.POST if request.method=='POST' else request.GET).get('camp')
    camp=get_object_or_404(camps,pk=camp_id) if camp_id else selected_camps.first()
    if camp is None:return render(request,template,{**extra,'rotation_filter':selector,'camps':camps,'day':day,'no_camp_match':True})
    workflow_action=request.POST.get('workflow_action','move') if request.method=='POST' else ''
    setup_direct=request.method=='POST' and workflow_action=='move' and request.user.role.code=='PERSONNEL_DISPATCHER' and request.POST.get('direct_fact') in {'1','on','true'}
    if request.method=='POST' and workflow_action=='move':
        try:
            housing.require_movement_day(request.user,day)
        except ValidationError as exc:raise PermissionDenied('; '.join(exc.messages))
    kind=request.GET.get('action','ARRIVAL');kind=kind if kind in {'ARRIVAL','DEPARTURE','TRANSFER','DISMISSAL'} else 'ARRIVAL'
    selected_employee=request.GET.get('employee','')
    if not selected_employee.isdigit() and request.GET.get('person','').isdigit():
        selected_employee=str(Person.objects.filter(company=request.user.company,pk=request.GET['person']).values_list('employee_id',flat=True).first() or '')
    from django.core import signing
    from apps.resources.models import Employee
    personal_token=request.POST.get('personal_scope','') if request.method=='POST' else ''
    if personal_token:
        try:
            personal=signing.loads(personal_token,salt='personnel-single-movement',max_age=12*3600)
            if personal.get('user')!=request.user.pk or personal.get('company')!=request.user.company_id:raise signing.BadSignature
            selected_employee=str(personal['employee'])
        except (signing.BadSignature,KeyError,TypeError):raise Http404('Персональная форма перемещения устарела.')
    fixed_employee=int(selected_employee) if selected_employee.isdigit() else None
    if fixed_employee:
        get_object_or_404(Employee,company=request.user.company,pk=fixed_employee)
        personal_token=signing.dumps({'user':request.user.pk,'company':request.user.company_id,'employee':fixed_employee},salt='personnel-single-movement')
    recommendation_day=timezone.localdate()
    recommendation_kind=request.POST.get('recommendation_kind','') if request.method=='POST' else ''
    arrival_recommendation_form=MovementRecommendationForm(request.POST if workflow_action=='recommendation_add' and recommendation_kind=='ARRIVAL' else None,
        company=request.user.company,day=recommendation_day,kind='ARRIVAL',prefix='arrival')
    departure_recommendation_form=MovementRecommendationForm(request.POST if workflow_action=='recommendation_add' and recommendation_kind=='DEPARTURE' else None,
        company=request.user.company,day=recommendation_day,kind='DEPARTURE',prefix='departure')
    if request.method=='POST' and workflow_action=='recommendation_add':
        recommendation_form=arrival_recommendation_form if recommendation_kind=='ARRIVAL' else departure_recommendation_form if recommendation_kind=='DEPARTURE' else None
        if recommendation_form is None:messages.error(request,'Неизвестное направление рекомендации.')
        elif recommendation_form.is_valid():
            from .movement_recommendations import add
            try:count=add(request.user,recommendation_kind,recommendation_form.cleaned_data['employees'],recommendation_form.cleaned_data['camp'],recommendation_form.cleaned_data['planned_on'])
            except ValidationError as exc:recommendation_form.add_error(None,'; '.join(exc.messages))
            else:
                messages.success(request,f'Рекомендации добавлены. Сотрудников: {count}.')
                return redirect('personnel:arrivals' if recommendation_kind=='ARRIVAL' else 'personnel:departures')
    if request.method=='POST' and (workflow_action in {'recommendation_remove','recommendation_request'} or request.POST.get('request_one')):
        from .movement_recommendations import create_requests,exclude
        tokens=[request.POST['request_one']] if request.POST.get('request_one') else request.POST.getlist('recommendations')
        action='recommendation_request' if workflow_action=='recommendation_request' or request.POST.get('request_one') else 'recommendation_remove'
        try:count=create_requests(request.user,tokens) if action=='recommendation_request' else exclude(request.user,tokens)
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        else:
            messages.success(request,(f'Заявки оформлены и отправлены в перевахту. Сотрудников: {count}.' if action=='recommendation_request' else f'Из рекомендаций убрано сотрудников: {count}.'))
            return redirect('personnel:arrivals' if recommendation_kind!='DEPARTURE' else 'personnel:departures')
    form=MovementForm(request.POST if request.method=='POST' and workflow_action=='move' else None,company=request.user.company,camp=camp,day=day,fixed_employee=fixed_employee,allow_direct=request.user.role.code=='PERSONNEL_DISPATCHER',
        initial={'personal_scope':personal_token,'kind':kind,'date':day,'camp':None if request.GET.get('choose_camp')=='1' and kind=='ARRIVAL' else camp.pk,'snapshot':housing.movement_snapshot(request.user),'employees':[selected_employee] if selected_employee.isdigit() else []})
    movement_exclusions=[]
    if request.method=='POST' and workflow_action=='move' and form.is_valid():
        try:
            selected=form.cleaned_data['employees'];arrival=form.cleaned_data['kind']=='ARRIVAL'
            people=[] if arrival else list(Person.objects.filter(company=request.user.company,employee__in=selected))
            direct=bool(form.cleaned_data.get('direct_fact'))
            operation=housing.move if direct else housing.request_moves
            options={'setup_direct':True} if direct else {}
            saved,movement_exclusions=housing.process_moves_partially(operation,request.user,form.cleaned_data['kind'],camp,form.cleaned_data['date'],people,
                employees=selected,target=form.cleaned_data.get('target'),comment=form.cleaned_data['comment'],snapshot=form.cleaned_data['snapshot'],departure_destination='DISMISSAL' if form.cleaned_data.get('destination')=='DISMISSAL' else 'REST',**options)
        except (ValidationError,IntegrityError) as exc:form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Размещение изменилось одновременно с вашим вводом. Обновите список.')
        else:
            count=len(saved)
            messages.success(request,(f'Сохранено фактических перемещений: {count}.' if direct else f'Создано заявок в перевахту: {count}.')+f' Исключено: {len(movement_exclusions)}.')
            for row in movement_exclusions:messages.warning(request,f'{row["employee"].display_name}: {row["reason"]}')
            from .rotation_calendar import cycle_definition,effective_schedule
            missing=0
            for person in Person.objects.filter(company=request.user.company,employee__in=saved).select_related('employee','schedule_slot__pattern'):
                assigned=effective_schedule(housing.schedule_slot(person),person)
                if not (assigned and assigned.pattern_id and cycle_definition(assigned.pattern)):missing+=1
            if missing:messages.warning(request,f'У {missing} сотрудников цикл вахта/отдых не задан. Укажите его через «Изменить график»; прогноз для них пока не пересчитан.')
            if not movement_exclusions:
                return redirect((reverse('personnel:movements')+'?camp='+str(camp.pk)+'&date='+str(form.cleaned_data['date'])+'#movement') if request.resolver_match.url_name=='movements' else camp_url(camp)+'#roster')
            retry=request.POST.copy();retry.setlist('employees',[str(row['employee'].pk) for row in movement_exclusions]);retry['snapshot']=housing.movement_snapshot(request.user)
            form=MovementForm(retry,company=request.user.company,camp=camp,day=day,fixed_employee=fixed_employee,allow_direct=request.user.role.code=='PERSONNEL_DISPATCHER')
    if movements:
        from .movement_recommendations import build
        recommendations=build(request.user.company,recommendation_day)
        events=HousingEvent.objects.filter(company=request.user.company).filter(Q(from_camp__in=selected_camps)|Q(to_camp__in=selected_camps)).select_related('person','from_camp','to_camp','recorded_by')[:50]
        return render(request,template,{**extra,'rotation_filter':selector,'day':day,'camp':camp,'camps':camps,'selected_camps':selected_camps,'form':form,'events':events,
            'movement_exclusions':movement_exclusions,
            'arrival_recommendations':recommendations['ARRIVAL'],'departure_recommendations':recommendations['DEPARTURE'],
            'arrival_recommendation_count':len(recommendations['ARRIVAL']),'departure_recommendation_count':len(recommendations['DEPARTURE']),
            'arrival_recommendation_form':arrival_recommendation_form,'departure_recommendation_form':departure_recommendation_form,**housing.movement_window_context(request.user,day)})
    values=selector.cleaned_data
    selected_camp_list=list(selected_camps)
    rows,days,rotation_stats=rotation_rows(request.user.company,selected_camp_list,day,query=values.get('q',''),state=values.get('state',''),include_unassigned=not values.get('construction_object') and not values.get('camps'))
    recommendation_rows=rows
    from .rotation import positions_in_rows,filter_position,rotation_stats as count_rotation
    available=positions_in_rows(request.user.company,rows)
    selector.fields['position'].queryset=available
    if values.get('position') and not available.filter(pk=values['position'].pk).exists():
        values['position']=None;selector.data=selector.data.copy();selector.data['position']=''
        messages.info(request,'Фильтр должности сброшен: её нет в текущей выборке.')
    rows=filter_position(rows,values.get('position'));rotation_stats=count_rotation(rows)
    from .rotation import status_indicator
    for row in rows:row['status_tone'],row['status_caption'],row['status_help']=status_indicator(row)
    # The calendar is a forecast through the end of the following year.
    calendar_start=day.replace(day=1);calendar_end=date(day.year+1,12,31)
    fragment=request.GET.get('calendar_fragment')=='1'
    calendar_dates=[];calendar_months=[]
    roster_params=request.GET.copy();roster_params.pop('date',None)
    calendar_params=roster_params.copy();calendar_params['calendar_fragment']='1';calendar_params.pop('calendar_page',None);calendar_params.pop('calendar_person',None)
    rotation_calendar_url=request.path+'?'+calendar_params.urlencode()
    if fragment:
        from django.core.paginator import Paginator
        paginator=Paginator(rows,50);page_number=request.GET.get('calendar_page')
        wanted=request.GET.get('calendar_person','')
        if wanted.isdigit():
            index=next((i for i,row in enumerate(rows) if row.get('person') and row['person'].pk==int(wanted)),None)
            if index is not None:page_number=index//50+1
        calendar_page=paginator.get_page(page_number)
        calendar_dates=[calendar_start+timedelta(days=n) for n in range((calendar_end-calendar_start).days+1)]
        for current in calendar_dates:
            if not calendar_months or calendar_months[-1]['start'].month!=current.month:calendar_months.append({'start':current,'span':0})
            calendar_months[-1]['span']+=1
        from .rotation_calendar import stays_by_person
        history=None
        for row in calendar_page:
            values_calendar=None
            if values_calendar is None:
                if history is None:history=stays_by_person(request.user.company)
                values_calendar=calendar_for(row.get('schedule_slot') or row.get('slot'),person=row.get('person'),start=calendar_start,end=calendar_end,stays=history.get(row['person'].pk,[]) if row.get('person') else [])
            row['calendar_codes']=[values_calendar.get(str(current),'') for current in calendar_dates]
        return render(request,'personnel/_rotation_calendar.html',{'rows':calendar_page.object_list,'day':day,'calendar_start':calendar_start,'calendar_end':calendar_end,'calendar_dates':calendar_dates,'calendar_months':calendar_months,'calendar_page':calendar_page,'calendar_query':calendar_params.urlencode()})
    events=HousingEvent.objects.filter(company=request.user.company).filter(Q(from_camp__in=selected_camps)|Q(to_camp__in=selected_camps)).select_related('person','from_camp','to_camp','recorded_by')[:50]
    from .housing_plan import monthly_plan,position_headers
    plan_context=monthly_plan(request.user,camp,day,camps=selected_camps)
    position_groups=position_headers(request.user.company,rows,plan_context,query=values.get('q',''),position=values.get('position'),state=values.get('state',''))
    from .housing_plan import roster_sections
    roster_view='groups' if raw.get('roster_view')=='groups' else 'positions'
    view_links=[]
    for value,label in [('positions','По должностям'),('groups','По группам должностей')]:
        params=roster_params.copy();params['roster_view']=value
        view_links.append({'value':value,'label':label,'url':'?'+params.urlencode()+'#roster'})
    from apps.projects.models import ConstructionObject
    objects=ConstructionObject.objects.filter(company=request.user.company,housing_camps__in=selected_camps).distinct()
    from .lodging_views import summary_context
    plan_context.update(summary_context(request.user,camp,day,camps=selected_camps))
    from .movement_recommendations import build as build_recommendations
    navigation_recommendations=build_recommendations(request.user.company,recommendation_day,camps=selected_camp_list,rows=recommendation_rows)
    return render(request,template,{**extra,**plan_context,'rotation_filter':selector,'rotation_stats':rotation_stats,'position_groups':position_groups,'selected_camps':selected_camps,
        'roster_view':roster_view,'roster_view_links':view_links,'roster_sections':roster_sections(position_groups,roster_view=='groups',request.user.company),
        'camp':camp,'camps':camps,'day':day,'rows':rows,'dates':days,'form':form,'query':values.get('q',''),
        'roster_query':roster_params.urlencode(),'rotation_calendar_url':rotation_calendar_url,'calendar_dates':calendar_dates,'calendar_months':calendar_months,'calendar_start':calendar_start,'calendar_end':calendar_end,'events':events,'total':rotation_stats['people'],'objects':objects,
        'unknown_count':Person.objects.filter(company=request.user.company,stays__isnull=True,housing_events__isnull=True).count(),
        'unlinked_count':Person.objects.filter(company=request.user.company,employee=None).count(),
        'arrival_recommendation_count':len(navigation_recommendations['ARRIVAL']),'departure_recommendation_count':len(navigation_recommendations['DEPARTURE']),**window_context(request.user,day)})


def review_request_context(request):
    tab='departure' if request.GET.get('kind')=='departure' else 'arrival'
    sort=request.GET.get('sort','created_desc')
    sort_options=[('created_desc','Новые заявки сверху'),('created_asc','Старые заявки сверху'),('name_asc','ФИО: А–Я'),('name_desc','ФИО: Я–А'),('movement_asc','Дата заезда / выезда: ранние сверху'),('movement_desc','Дата заезда / выезда: поздние сверху')]
    if sort not in dict(sort_options):sort='created_desc'
    raw_date=request.GET.get('date','').strip();filter_date=None
    if raw_date:
        try:filter_date=forms.DateField().clean(raw_date)
        except ValidationError:raw_date=''
    base=MovementReviewRequest.objects.filter(company=request.user.company)
    qs=base.filter(kind='ARRIVAL') if tab=='arrival' else base.exclude(kind='ARRIVAL')
    if filter_date:qs=qs.filter(movement_date=filter_date)
    from django.db.models import Case,When,Value,IntegerField
    ordering={'created_desc':['-created_at','-pk'],'created_asc':['created_at','pk'],'name_asc':['person__full_name','pk'],'name_desc':['-person__full_name','-pk'],
        'movement_asc':['movement_date','pk'],'movement_desc':['-movement_date','-pk']}[sort]
    rows=list(qs.select_related('person__employee','from_camp','to_camp','requested_by','event').annotate(
        open_order=Case(When(status='OPEN',then=Value(0)),default=Value(1),output_field=IntegerField())).order_by('open_order',*ordering)[:1000])
    dispatcher_rows=[row for row in rows if row.source=='DISPATCHER'];excel_rows=[row for row in rows if row.source=='EXCEL']
    return {'personnel_section':'reviews','movement_review_requests':rows,'dispatcher_review_requests':dispatcher_rows,'excel_review_requests':excel_rows,'review_tab':tab,'review_arrival':tab=='arrival','review_date':raw_date,'review_sort':sort,'review_sort_options':sort_options,
        'arrival_review_requests':rows if tab=='arrival' else [],'departure_review_requests':rows if tab=='departure' else [],
        'arrival_review_count':base.filter(kind='ARRIVAL').count(),'departure_review_count':base.exclude(kind='ARRIVAL').count(),
        'movement_review_open_count':base.filter(status='OPEN').count()}


@login_required
def movement_reviews(request):
    access(request)
    result=None
    if request.method=='POST' and request.POST.get('review_action')=='import_excel':
        uploaded=request.FILES.get('movement_excel')
        if not uploaded:messages.error(request,'Выберите Excel-файл перевахты.')
        else:
            try:
                from .movement_excel_import import apply
                result=apply(request.user,uploaded.read())
                messages.success(request,f"Импорт завершён. Создано заявок: {result['created']}; повторов пропущено: {result['duplicates']}; не сопоставлено: {len(result['unmatched'])}.")
            except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return render(request,'personnel/movement_reviews.html',{**review_request_context(request),'movement_import_result':result})


@login_required
@require_http_methods(['GET','POST'])
def movement_review_accept(request,pk):
    from core.permissions import require_roles,role_code
    require_roles(request.user,{'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER','MANAGER'})
    row=get_object_or_404(MovementReviewRequest.objects.select_related('person','from_camp','to_camp','event'),pk=pk,company=request.user.company)
    workflow_action=request.POST.get('workflow_action') if request.method=='POST' else None
    if workflow_action:
        from .departure_workflow import arrive_hub,leave_hub,resubmit,update_hub
        try:
            if workflow_action=='resubmit':resubmit(request.user,row)
            elif workflow_action=='arrive_hub':arrive_hub(request.user,row,forms.DateField().clean(request.POST.get('hub_arrived_on')))
            elif workflow_action=='hub_documents':update_hub(request.user,row,tickets_ready=request.POST.get('tickets_ready')=='1',clearance_document=request.FILES.get('clearance_document'))
            elif workflow_action=='leave_hub':leave_hub(request.user,row,forms.DateField().clean(request.POST.get('hub_departed_on')))
            else:raise ValidationError('Неизвестное действие по заявке.')
        except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        else:messages.success(request,{'resubmit':'Заявка повторно отправлена руководителю.','arrive_hub':'Прибытие в ОП Дудинка зафиксировано.','hub_documents':'Данные по билетам и обходному листу сохранены.','leave_hub':'Выезд из ОП Дудинка зафиксирован. Заявка завершена.'}[workflow_action])
        return redirect('personnel:movement_review_accept',pk=row.pk)
    edit_mode=row.status in {'OPEN','RETURNED'} or request.GET.get('edit')=='1' or request.method=='POST'
    confirm=request.method=='POST' and request.POST.get('review_action')=='confirm'
    form=MovementReviewForm(request.POST if request.method=='POST' else None,instance=row,confirm=confirm)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            locked=MovementReviewRequest.objects.select_for_update().get(pk=row.pk,company=request.user.company)
            if locked.status=='ACCEPTED' and confirm:
                messages.info(request,'Заявка уже подтверждена.');return redirect(reverse('personnel:movement_reviews')+'#movement-requests')
            for name in form._meta.fields:setattr(locked,name,form.cleaned_data[name])
            camp=form.cleaned_data.get('camp')
            if locked.kind=='ARRIVAL':locked.to_camp=camp
            else:locked.from_camp=camp
            if locked.site_object_id:locked.site=locked.site_object.name
            selected_city=form.cleaned_data.get('base_city_choice');new_city=form.cleaned_data.get('new_base_city','')
            if new_city:
                from .models import BaseCity
                selected_city=BaseCity.objects.filter(company=request.user.company,name__iexact=new_city).first() or BaseCity.objects.create(company=request.user.company,name=new_city)
            if selected_city:locked.base_city=selected_city.name
            if confirm:
                locked.status='ACCEPTED';locked.reviewed_by=request.user;locked.reviewed_at=timezone.now()
            locked.save()
        messages.success(request,'Заявка перевахты подтверждена.' if confirm else 'Данные заявки сохранены.')
        if row.status=='ACCEPTED' and not confirm:return redirect('personnel:movement_review_accept',pk=row.pk)
        return redirect(reverse('personnel:movement_reviews')+'#movement-requests')
    from .models import CampObjectLink
    from apps.projects.models import ConstructionObject
    tree={obj.pk:obj.parent_id for obj in ConstructionObject.objects.filter(company=request.user.company)};pairs={}
    for object_id,camp_id in CampObjectLink.objects.filter(company=request.user.company,camp__is_active=True).values_list('construction_object_id','camp_id'):
        current=object_id;seen=set()
        while current and current not in seen:
            seen.add(current);pairs.setdefault(str(current),set()).add(camp_id);current=tree.get(current)
    return render(request,'personnel/movement_review.html',{'row':row,'form':form,'edit_mode':edit_mode,'can_manage_workflow':role_code(request.user) in {'ADMIN','HR_HEAD','PERSONNEL_DISPATCHER'},'personnel_section':'reviews','project_camps':{key:sorted(value) for key,value in pairs.items()}})



@login_required
def history(request,pk):
    person=get_object_or_404(scoped(request,Person),pk=pk)
    stays=person.stays.select_related('camp','recorded_by','cancelled_by')
    for stay in stays:stay.can_cancel=can_edit_fact_day(request.user,stay.arrived_on)
    events=person.housing_events.select_related('from_camp','to_camp','recorded_by')
    from .plan_assignments import assignment_history
    return render(request,'personnel/housing_history.html',{'person':person,'stays':stays,'events':events,'assignments':assignment_history(person)})


@login_required
@require_http_methods(['GET','POST'])
def cancel(request,pk):
    stay=get_object_or_404(scoped(request,CampStay).select_related('person','camp'),pk=pk)
    try:require_fact_day(request.user,stay.arrived_on)
    except ValidationError as exc:raise PermissionDenied('; '.join(exc.messages))
    class CancelForm(forms.Form):
        reason=forms.CharField(label='Причина отмены',max_length=500,widget=forms.Textarea(attrs={'class':'form-control','rows':3}))
        version=forms.CharField(widget=forms.HiddenInput)
    form=CancelForm(request.POST if request.method=='POST' else None,initial={'version':stay.updated_at.isoformat()})
    if request.method=='POST' and form.is_valid():
        try:housing.cancel_stay(request.user,stay,form.cleaned_data['reason'],form.cleaned_data['version'])
        except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
        else:messages.success(request,'Размещение отменено. История сохранена.');return redirect('personnel:housing_history',pk=stay.person_id)
    return render(request,'personnel/form.html',{'form':form,'title':f'Отменить размещение: {stay}'})


@login_required
@require_http_methods(['GET','POST'])
def schedule(request,pk):
    person=get_object_or_404(scoped(request,Person).select_related('employee__position','schedule_slot'),pk=pk)
    from core.permissions import role_code
    manual_allowed=role_code(request.user)=='ADMIN'
    request_mode=request.method=='POST' and request.POST.get('schedule_action')=='request_zup'
    if request.method=='POST' and not request_mode and not manual_allowed:raise PermissionDenied('График сотрудника меняется через заявку в ЗУП.')
    request_form=ZupScheduleChangeForm(request.POST if request_mode else None,person=person)
    form=PersonScheduleForm(request.POST if request.method=='POST' and not request_mode else None,person=person)
    if request_mode and request_form.is_valid():
        from .schedule_changes import request_change
        try:request_change(request.user,person,request_form.cleaned_data['schedule'])
        except ValidationError as exc:request_form.add_error(None,'; '.join(exc.messages))
        else:
            messages.success(request,'Заявка создана. График изменится после подтверждения изменения очередной синхронизацией из ЗУП.')
            current=housing.present_stays(person.company,timezone.localdate()).filter(person=person).select_related('camp').first()
            camp_id=current.camp_id if current else person.schedule_slot.housing_camp_id if person.schedule_slot_id else None
            params={}
            if camp_id:params['camp']=camp_id
            params['q']=person.display_name
            return redirect(reverse('personnel:housing')+'?'+urlencode(params)+'#roster')
    if request.method=='GET' and request.GET.get('slot','').isdigit():
        selected=person.assignments.filter(company=person.company,pk=request.GET['slot'],cancelled_at=None).first()
        if selected:form.initial.update(slot=selected.pk,pattern=selected.pattern_id)
    if request.method=='POST' and not request_mode and form.is_valid():
        with transaction.atomic():
            from apps.accounts.models import Company
            from django.db.models import F
            Company.objects.filter(pk=request.user.company_id).update(name=F('name'))
            person=Person.objects.select_for_update().get(pk=person.pk,company=request.user.company)
            slot=form.cleaned_data.get('slot');pattern=form.cleaned_data.get('pattern')
            if slot:
                slot=StaffingSlot.objects.select_for_update().get(pk=slot.pk,person=person,company=person.company)
                if slot.cancelled_at:form.add_error('slot','Назначение удалено. Обновите страницу графика.')
                if pattern and pattern.board_id!=slot.group.board_id:
                    form.add_error('pattern','График должен принадлежать таблице выбранной строки. Выберите другую строку или очистите выбор строки.')
            if not form.errors:
                try:housing.check_schedule(person,form.cleaned_data['version'],slot)
                except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
            if not form.errors:
                if slot and slot.group.board.import_digest:
                    # Personal changes must not rewrite the imported opening balance.
                    from .models import ShiftPattern
                    board=Board.objects.filter(company=person.company,name='Графики сотрудников ВЖГ',import_digest='').order_by('pk').first()
                    if board is None:board=Board.objects.create(company=person.company,name='Графики сотрудников ВЖГ')
                    position=str(person.employee.position) if person.employee_id and person.employee.position_id else slot.group.position
                    group=board.groups.filter(position=position).first() or StaffingGroup.objects.create(company=person.company,board=board,position=position)
                    original=slot
                    if pattern and not form.cleaned_data.get('on_days'):pattern=ShiftPattern.objects.create(company=person.company,board=board,name=pattern.name,kind=pattern.kind,calendar=pattern.calendar,on_days=pattern.on_days,off_days=pattern.off_days,cycle_codes=pattern.cycle_codes)
                    slot=StaffingSlot(company=person.company,group=group,person=person,housing_camp=original.housing_camp,camp=original.camp,status=original.status,arrival_date=original.arrival_date,departure_date=original.departure_date,calendar=dict(original.calendar),comment='Личный график; начальные данные сохранены в исходной таблице.')
                if not slot:
                    board=pattern.board if pattern else Board.objects.filter(company=person.company,name='Графики сотрудников ВЖГ',import_digest='').order_by('pk').first()
                    if board is None:board=Board.objects.create(company=person.company,name='Графики сотрудников ВЖГ')
                    position=str(person.employee.position) if person.employee_id and person.employee.position_id else 'Без должности'
                    group=board.groups.filter(position=position).first() or StaffingGroup.objects.create(company=person.company,board=board,position=position)
                    slot=StaffingSlot(company=person.company,group=group,person=person)
                if form.cleaned_data.get('on_days'):
                    from .models import ShiftPattern
                    from .rotation_calendar import cycle_definition,work
                    from collections import Counter
                    cycle=cycle_definition(pattern)
                    codes=cycle['codes'] if cycle else list(pattern.calendar.values()) if pattern else []
                    defaults=Counter(str(code) for code in codes if work(code)).most_common(1)
                    hours=str(form.cleaned_data['hours']) if form.cleaned_data.get('hours') is not None else defaults[0][0] if defaults else 'МВ'
                    on,off=form.cleaned_data['on_days'],form.cleaned_data['off_days']
                    pattern=ShiftPattern.objects.create(company=person.company,board=slot.group.board,name=f'Индивидуальный ({on}/{off})',kind=pattern.kind if pattern else '',on_days=on,off_days=off,cycle_codes=[hours]*on+['МО']*off)
                slot.pattern=pattern
                start,end=form.cleaned_data.get('start'),form.cleaned_data.get('end')
                if start:
                    calendar=dict(slot.calendar);code=form.cleaned_data.get('code','');d=start
                    while d<=end:
                        if code:calendar[d.isoformat()]=code
                        else:calendar.pop(d.isoformat(),None)
                        d+=timedelta(days=1)
                    slot.calendar=calendar
                slot.save();person.schedule_slot=slot;person.save(update_fields=['schedule_slot','updated_at'])
                messages.success(request,'График сотрудника сохранён. Размещение в ВЖГ не изменено.');return redirect('personnel:housing_history',pk=person.pk)
    pending=person.schedule_change_requests.filter(status='PENDING').select_related('requested_schedule','requested_by').first()
    return render(request,'personnel/person_schedule.html',{'form':form,'request_form':request_form,'person':person,'pending_schedule_change':pending,'manual_allowed':manual_allowed,'title':'График: '+person.display_name})


@login_required
@require_http_methods(['POST'])
def link_people(request):
    access(request);count=housing.link_people(request.user.company)
    messages.success(request,f'Связано карточек: {count}. Неоднозначные совпадения оставлены для ручного уточнения.');return redirect('personnel:people')


def workbook_response(filename,title,rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill
    book=Workbook();sheet=book.active;sheet.title=title
    for row in rows:sheet.append(["'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v for v in row])
    sheet.freeze_panes='A2';sheet.auto_filter.ref=sheet.dimensions
    for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='167A94')
    for col in sheet.columns:sheet.column_dimensions[col[0].column_letter].width=26
    stream=BytesIO();book.save(stream);response=HttpResponse(stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']=f'attachment; filename="{filename}"';return response


@login_required
def roster_export(request):
    from .rotation import RotationFilter,normalize_rotation_filter_data,rotation_rows
    raw=request.GET.copy();raw['date']=str(timezone.localdate());raw=normalize_rotation_filter_data(raw,request.user.company)
    selector=RotationFilter(raw,company=request.user.company)
    if not selector.is_valid():raise Http404('Некорректные фильтры перевахты.')
    camps=selector.selected_camps(request.user.company);data=selector.cleaned_data;day=data['date']
    rows,days,_=rotation_rows(request.user.company,list(camps),day,query=data.get('q',''),position=data.get('position'),state=data.get('state',''),include_unassigned=not data.get('construction_object') and not data.get('camps'))
    values=[['ВЖГ','Дата','Сотрудник / вакансия','Табельный номер','Должность','Вахта / отдых','Заезд','Выезд','Выезд по графику','Возвращение по графику','Дней на вахте / МО','Начало текущей вахты / МО','Источник начала периода','График',*[d.strftime('%d.%m.%Y') for d in days]]]
    for row in rows:
        person=row['person'];fmt=lambda value:value.isoformat() if value else ''
        values.append([str(row['camp'] or ''),str(day),person.display_name if person else 'Вакансия',person.display_employee_number if person else '',row['position_label'],row['state_label'],fmt(row.get('arrived_on')),fmt(row.get('last_departure')),fmt(row.get('planned_departure_display')),fmt(row.get('planned_return')),row.get('state_days'),fmt(row.get('state_since')),row.get('state_days_source',''),row.get('schedule_label',''),*row['codes']])
    return workbook_response('rotation.xlsx','Перевахта',values)



@login_required
@require_http_methods(['GET'])
def individual_calendar(request,pk):
    from datetime import timedelta
    from django.http import JsonResponse
    from .services import is_work
    person=get_object_or_404(scoped(request,Person).select_related('employee','schedule_slot'),pk=pk)
    raw=request.GET.get('month',timezone.localdate().strftime('%Y-%m'))
    try:month=date.fromisoformat(raw+'-01')
    except ValueError:return JsonResponse({'error':'Укажите месяц в формате ГГГГ-ММ.'},status=400)
    slot=housing.schedule_slot(person);calendar=calendar_for(slot,person=person,start=month,end=month+timedelta(days=730))
    last=monthrange(month.year,month.month)[1]
    days=[month.replace(day=n) for n in range(1,last+1)]
    previous=(month-timedelta(days=1)).replace(day=1);following=(month+timedelta(days=32)).replace(day=1)
    from .rotation_calendar import description,schedule_label
    return JsonResponse({'name':person.display_name,'number':person.display_employee_number,'month':month.strftime('%Y-%m'),
        'schedule_info':description(slot,min(max(timezone.localdate(),month),days[-1]),person=person),
        'pattern':schedule_label(slot,person),
        'offset':month.weekday(),'previous':previous.strftime('%Y-%m'),'next':following.strftime('%Y-%m'),
        'edit_url':reverse('personnel:person_schedule',args=[person.pk]),
        'days':[{'day':d.day,'date':str(d),'code':calendar.get(str(d),''),'work':is_work(calendar.get(str(d),'')),'today':d==timezone.localdate()} for d in days]})


def movement_context(request):
    from .models import ArrivalPreparation
    from django.core.paginator import Paginator
    rows=ArrivalPreparation.objects.filter(company=request.user.company,candidate__request__cancelled_at__isnull=True,status__in=['EXPECTED','ARRIVED']).filter(Q(actual_stay__isnull=True)|Q(actual_stay__cancelled_at__isnull=False)).select_related('candidate__request__construction_object','camp','employee','actual_stay').order_by('expected_on','pk')
    return {'personnel_section':'movements','arrival_queue':Paginator(rows,25).get_page(request.GET.get('arrivals_page'))}


@login_required
@require_http_methods(['GET','POST'])
def movements(request):
    return workspace(request,movements=True,movement_page='new')


@login_required
@require_http_methods(['GET','POST'])
def arrivals(request):
    return workspace(request,movements=True,movement_page='arrival')


@login_required
@require_http_methods(['GET','POST'])
def departures(request):
    return workspace(request,movements=True,movement_page='departure')
