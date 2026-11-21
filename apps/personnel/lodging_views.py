import json
import logging
import os
import subprocess
import sys
from collections import Counter
from datetime import date,datetime,time
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction,IntegrityError
from django.db.models import Count,Q
from django.http import JsonResponse,Http404
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.conf import settings
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from core.permissions import require_roles,role_code
from core.fact_edit_window import can_edit_fact_day
from apps.resources.models import Employee
from .models import Camp,LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent
from . import lodging,lodging_sync
from .lodging_forms import LodgingFilter,LodgingMovementForm,LodgingReportFilter,BuildingForm,BedForm,CorrectionForm,QuickBedForm

PERFORMANCE=logging.getLogger('gpr.performance')


def measured(response,label,started,**parts):
    total=(perf_counter()-started)*1000
    values={**parts,'total':total};response['Server-Timing']=', '.join(f'{name};dur={value:.1f}' for name,value in values.items())
    if total>=1000:PERFORMANCE.warning('%s %.0f ms: %s',label,total,{key:round(value) for key,value in values.items()})
    return response


def parameters(request):
    source=request.POST if request.method=='POST' else request.GET
    if not str(source.get('camp','')).isdigit():raise Http404('Укажите местонахождение.')
    camp=get_object_or_404(Camp,company=request.user.company,pk=source.get('camp'))
    try:day=date.fromisoformat(source.get('date',''))
    except (ValueError,TypeError):day=timezone.localdate()
    return camp,day


def board_url(camp,day=None):return reverse('personnel:lodging')+'?'+urlencode({'camp':camp.pk})


def lodging_place_url(camp, day, bed):
    query = urlencode({
        'camp': camp.pk,
        'focus_building': bed.building_id, 'focus_bed': bed.pk,
    })
    return f'{reverse("personnel:lodging")}?{query}#lodging-bed-{bed.pk}'


def lodging_place_label(bed):
    place = (f'комн. {bed.room} · ' if bed.room else '') + f'место {bed.number}'
    return f'{lodging.building_title(bed.building)} · {place}'


def movement_success(kind, employee, camp, day, old_bed=None, new_bed=None):
    name = employee.display_name
    if kind == 'CHECKIN':
        place = format_html('<a href="{}">Показать место: {}</a>', lodging_place_url(camp, day, new_bed), lodging_place_label(new_bed))
        return format_html('Заселение сотрудника <strong>{}</strong> выполнено успешно. {}', name, place)
    if kind == 'CHECKOUT':
        place = format_html('<a href="{}">Освободившееся место: {}</a>', lodging_place_url(camp, day, old_bed), lodging_place_label(old_bed))
        return format_html('Выселение сотрудника <strong>{}</strong> выполнено успешно. {}', name, place)
    if kind == 'MOVE':
        previous = format_html('<a href="{}">Старое место: {}</a>', lodging_place_url(camp, day, old_bed), lodging_place_label(old_bed))
        current = format_html('<a href="{}">Новое место: {}</a>', lodging_place_url(camp, day, new_bed), lodging_place_label(new_bed))
        return format_html('Переселение сотрудника <strong>{}</strong> выполнено успешно. {} · {}', name, previous, current)
    return format_html('Сведения сотрудника <strong>{}</strong> сохранены.', name)


def presence_session(request,camp,provided=None):
    """Keep one presence id per browser session and location across all lodging pages."""
    key=f'lodging_presence_{camp.pk}'
    value=provided or request.session.get(key) or uuid4()
    value=str(lodging.session_id(value))
    request.session[key]=value
    return value


def touch_presence(request,camp,provided=None):
    session=presence_session(request,camp,provided)
    return lodging.presence(request.user,camp,session),session


def presence_context(request,camp,provided=None):
    state,session=touch_presence(request,camp,provided)
    return {'presence_state':state,'presence_session':session,'editable':state['editor']}


def editor_presence(request,camp,provided=None):
    context=presence_context(request,camp,provided)
    if not context['editable']:
        raise PermissionDenied(f'Только просмотр: местонахождение сейчас редактирует {context["presence_state"]["editor_name"] or "другой комендант"}.')
    return context


def editor_error(state):
    return f'Только просмотр: местонахождение сейчас редактирует {state["editor_name"] or "другой комендант"}.'


@login_required
@require_POST
def employee_gender(request,pk):
    require_roles(request.user,lodging.OPERATORS)
    camp,day=parameters(request)
    editor_presence(request,camp,request.POST.get('presence_session'))
    value=request.POST.get('gender','')
    if value not in dict(Employee._meta.get_field('gender').choices):return JsonResponse({'error':'Выберите пол из списка.'},status=400)
    with transaction.atomic():
        employee=get_object_or_404(Employee.objects.select_for_update(),company=request.user.company,pk=pk)
        if not lodging.present(request.user.company,day).filter(camp=camp,employee=employee).exists():raise Http404('Сотрудник не проживает в выбранном местонахождении.')
        if employee.gender!=value:
            from apps.audit.models import AuditLog
            previous=employee.gender;employee.gender=value;employee.save(update_fields=['gender','updated_at'])
            AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='resources.Employee',entity_id=str(employee.pk),old_value={'gender':previous},new_value={'gender':value},comment='Уточнение пола сотрудника в учёте коменданта')
    messages.success(request,'Пол сотрудника сохранён в карточке.')
    return redirect(board_url(camp,day)+'#places')


def employee_company(employee):return employee.zup_organisation or str(employee.company)


@login_required
@require_GET
def available_beds(request):
    started=perf_counter()
    require_roles(request.user,lodging.OPERATORS)
    camp,day=parameters(request)
    try:day=date.fromisoformat(request.GET.get('date',''))
    except ValueError:return JsonResponse({'error':'Укажите корректную дату.'},status=400)
    try:session=lodging.session_id(request.GET.get('session')) if request.GET.get('session') else None
    except ValidationError:return JsonResponse({'error':'Обновите форму оформления.'},status=400)
    beds=lodging.available_beds(request.user.company,camp,day,user=request.user,session=session)
    return measured(JsonResponse({'beds':[{'id':bed.pk,'label':lodging.bed_label(bed),'building_id':bed.building_id,'building_gender':bed.building.gender} for bed in beds]}),'lodging available beds',started)


@login_required
@require_GET
def filter_beds(request):
    """Search places for the board filters without rendering the full catalogue."""
    require_roles(request.user,lodging.READERS)
    if not str(request.GET.get('camp','')).isdigit():return JsonResponse({'items':[],'page':1,'pages':1})
    camp=get_object_or_404(Camp,company=request.user.company,pk=request.GET['camp'])
    beds=LodgingBed.objects.filter(company=request.user.company,building__camp=camp).select_related('building')
    building_ids=[value for value in request.GET.getlist('building') if value.isdigit()]
    if building_ids:beds=beds.filter(building_id__in=building_ids)
    for term in request.GET.get('q','').split():
        beds=beds.filter(Q(building__name__icontains=term)|Q(building__description__icontains=term)|Q(building__inventory_number__icontains=term)|Q(room__icontains=term)|Q(number__icontains=term))
    page=Paginator(beds.order_by('building__name','room','number','pk'),100).get_page(request.GET.get('page'))
    return JsonResponse({'items':[{'id':bed.pk,'label':lodging.bed_filter_label(bed),'building_id':bed.building_id} for bed in page.object_list],
        'page':page.number,'pages':page.paginator.num_pages})


def place_rows(company,camp,day,building=None,bed_ids=None):
    occupied_query=lodging.present(company,day).filter(camp=camp)
    if building is not None:occupied_query=occupied_query.filter(bed__building=building)
    if bed_ids is not None:occupied_query=occupied_query.filter(bed_id__in=bed_ids)
    occupied={stay.bed_id:stay for stay in occupied_query.select_related('employee__position__planning_mapping__brigade__group','employee__company','bed__building','recorded_by')}
    from apps.resources.group_order import position_group
    rows=[]
    beds=LodgingBed.objects.filter(company=company,building__camp=camp)
    if building is not None:beds=beds.filter(building=building)
    if bed_ids is not None:beds=beds.filter(pk__in=bed_ids)
    bed_rows=list(beds.select_related('building'))
    for bed in bed_rows:
        stay=occupied.get(bed.pk);status='OCCUPIED' if stay else 'UNAVAILABLE' if not bed.building.is_active else 'FREE' if bed.state=='AVAILABLE' else bed.state
        rows.append({'bed':bed,'stay':stay,'status':status,'status_label':{'OCCUPIED':'Занято','FREE':'Свободно','RESERVED':'Зарезервировано','UNAVAILABLE':'Недоступно'}[status],
            'organization':(stay.organization_snapshot or employee_company(stay.employee)) if stay else '',
            'position':(stay.position_snapshot if stay.organization_snapshot else stay.employee.display_position) if stay else '',
            'gender':stay.employee.get_gender_display() if stay else '',
            'position_group':position_group(stay.employee.position) if stay else '',
            'department':(stay.department_snapshot if stay.organization_snapshot else stay.employee.display_department) if stay else '',
            'overdue':bool(stay and stay.planned_departure and stay.planned_departure<day)})
    return rows


def filtered_place_rows(request,camp,day,building=None):
    bed_ids=[value for value in request.GET.getlist('beds') if value.isdigit()]
    rows=place_rows(request.user.company,camp,day,building,bed_ids or None)
    q=request.GET.get('q','').casefold().split();state=request.GET.get('state','')
    return [r for r in rows if (not state or r['status']==state) and all(term in ' '.join([str(r['bed']),lodging.building_title(r['bed'].building),r['stay'].employee.display_name if r['stay'] else '',r['stay'].employee.employee_number if r['stay'] else '',r['organization']]).casefold() for term in q)]


def aggregate_place_counts(company,camp,building_ids=None):
    """Two grouped SQL queries; no place or employee rows are materialised."""
    catalogue=LodgingBed.objects.filter(company=company,building__camp=camp)
    occupied=lodging.present(company).filter(camp=camp)
    if building_ids:
        catalogue=catalogue.filter(building_id__in=building_ids)
        occupied=occupied.filter(bed__building_id__in=building_ids)
    catalogue=catalogue.values('building_id','building__is_active','state').annotate(total=Count('pk'))
    occupied=occupied.values('bed__building_id','bed__building__is_active','bed__state').annotate(total=Count('pk'))
    groups={}
    for row in catalogue:
        item=groups.setdefault(row['building_id'],Counter());item['TOTAL']+=row['total']
        item[('CATALOGUE',row['building__is_active'],row['state'])]+=row['total']
    for row in occupied:
        item=groups.setdefault(row['bed__building_id'],Counter());item['OCCUPIED']+=row['total']
        item[('OCCUPIED',row['bed__building__is_active'],row['bed__state'])]+=row['total']
    stats=Counter()
    for item in groups.values():
        for composite,total in list(item.items()):
            if not isinstance(composite,tuple):continue
            prefix,active,state=composite
            if prefix!='CATALOGUE':continue
            occupied_count=item.get(('OCCUPIED',active,state),0)
            remaining=max(0,total-occupied_count)
            target='UNAVAILABLE' if not active else 'FREE' if state=='AVAILABLE' else state
            item[target]+=remaining
        for key in ('OCCUPIED','FREE','RESERVED','UNAVAILABLE'):stats[key]+=item[key]
    return stats,groups


@login_required
@require_GET
def building_places(request,pk):
    started=perf_counter()
    require_roles(request.user,lodging.READERS)
    camp,day=parameters(request)
    building=get_object_or_404(LodgingBuilding,company=request.user.company,camp=camp,pk=pk)
    group={'building':building,'places':filtered_place_rows(request,camp,day,building)}
    editable=lodging.editor_state(request.user,camp)[0]
    return measured(render(request,'personnel/_lodging_building_places.html',{'group':group,'camp':camp,'day':day,
        'editable':editable,'catalogue_editable':editable,'presence_session':request.session.get(f'lodging_presence_{camp.pk}','')}),'lodging building places',started)


@login_required
@require_GET
def event_history(request):
    started=perf_counter()
    require_roles(request.user,lodging.READERS)
    camp,day=parameters(request)
    events=LodgingEvent.objects.filter(company=request.user.company,camp=camp).select_related('employee','from_bed__building','to_bed__building','recorded_by','stay')
    page=Paginator(events,50).get_page(request.GET.get('page'))
    return measured(render(request,'personnel/_lodging_event_history.html',{'events':page.object_list,'page_obj':page,'camp':camp,'day':day,
        'catalogue_editable':lodging.editor_state(request.user,camp)[0]}),'lodging history',started)


def historical_occupancy(company,camp,day,mode):
    if mode=='FACTUAL':
        stays=LodgingStay.objects.filter(company=company,camp=camp,cancelled_at=None,arrived_on__lte=day).filter(Q(departed_on=None)|Q(departed_on__gt=day))
        return list(stays.select_related('employee','bed__building','recorded_by')),{}
    cutoff=timezone.make_aware(datetime.combine(day,time.max),timezone.get_current_timezone())
    events=LodgingEvent.objects.filter(company=company,camp=camp,created_at__lte=cutoff).select_related('stay').order_by('created_at','pk')
    current={};applied={}
    for event in events:
        applied[event.stay_id]=event.created_at
        if event.kind in {'CHECKIN','MOVE'} and event.to_bed_id:current[event.employee_id]=event.stay_id
        elif event.kind=='CHECKOUT':current.pop(event.employee_id,None)
        elif event.kind=='CANCEL' and current.get(event.employee_id)==event.stay_id:current.pop(event.employee_id,None)
    stays=LodgingStay.objects.filter(company=company,pk__in=current.values()).select_related('employee','bed__building','recorded_by')
    return list(stays),applied


@login_required
@require_GET
def historical_report(request):
    started=perf_counter()
    require_roles(request.user,lodging.READERS)
    camps=Camp.objects.filter(company=request.user.company)
    initial={'date':timezone.localdate(),'mode':'PORTAL'}
    if not request.GET and camps.exists():initial['camp']=camps.first()
    form=LodgingReportFilter(request.GET or None,company=request.user.company,initial=initial)
    rows=[];stats=Counter();warning='';camp=None;day=initial['date'];mode='PORTAL'
    if form.is_valid():
        camp=form.cleaned_data['camp'];day=form.cleaned_data['date'];mode=form.cleaned_data['mode'];building=form.cleaned_data['building'];state=form.cleaned_data['state'];terms=form.cleaned_data['q'].casefold().split()
        stays,applied=historical_occupancy(request.user.company,camp,day,mode);by_bed={}
        for stay in stays:by_bed.setdefault(stay.bed_id,[]).append(stay)
        if any(len(items)>1 for items in by_bed.values()):warning='По введённым фактическим датам обнаружены пересечения. Они показаны в отчёте и требуют проверки.'
        beds=LodgingBed.objects.filter(company=request.user.company,building__camp=camp).select_related('building')
        if building:beds=beds.filter(building=building)
        for bed in beds:
            occupants=by_bed.get(bed.pk,[])
            status='OCCUPIED' if occupants else 'UNAVAILABLE' if not bed.building.is_active else 'FREE' if bed.state=='AVAILABLE' else bed.state
            occupants=occupants or [None]
            for stay in occupants:
                row={'bed':bed,'stay':stay,'status':status,'status_label':{'OCCUPIED':'Занято','FREE':'Свободно','RESERVED':'Зарезервировано','UNAVAILABLE':'Недоступно'}[status],
                    'applied_at':applied.get(stay.pk) if stay else None,'overlap':len(by_bed.get(bed.pk,[]))>1}
                haystack=' '.join([str(bed),lodging.building_title(bed.building),stay.employee.display_name if stay else '',stay.employee.employee_number if stay else '']).casefold()
                if (not state or status==state) and all(term in haystack for term in terms):rows.append(row);stats[status]+=1
        if request.GET.get('export')=='xlsx':
            from .housing_views import workbook_response
            values=[['Дата отчёта','Режим','Местонахождение','Вагон / корпус','Комната','Место','Состояние','Сотрудник','Табельный номер','Дата заселения','Дата выселения','Сохранено в портале','Автор','Примечание']]
            for row in rows:
                stay=row['stay'];values.append([day,form.fields['mode'].choices[0][1] if mode=='PORTAL' else form.fields['mode'].choices[1][1],camp.name,row['bed'].building.name,row['bed'].room,row['bed'].number,row['status_label'],stay.employee.display_name if stay else '',stay.employee.employee_number if stay else '',stay.arrived_on if stay else '',stay.departed_on if stay else '',row['applied_at'] or '',(stay.recorded_by.get_full_name() or stay.recorded_by.username) if stay and stay.recorded_by else '',stay.comment if stay else row['bed'].note])
            return measured(workbook_response(f'lodging-{day}.xlsx','Состояние проживания',values),'lodging historical report export',started)
    response=render(request,'personnel/lodging_report.html',{'form':form,'rows':rows,'stats':stats,'camp':camp,'day':day,'mode':mode,'warning':warning})
    return measured(response,'lodging historical report',started)


def summary_context(user,camp,day,*,camps=None):
    selected=list(camps) if camps is not None else [camp]
    query={'camp':camp.pk,'date':str(day)}
    if len(selected)>1:query['camps']=[c.pk for c in selected]
    states=[lodging_sync.state_for(user.company,item,day) for item in selected]
    current=all(state.current for state in states);busy=any(state.status in {'QUEUED','RUNNING'} for state in states)
    calculated=max((state.calculated_at for state in states if state.calculated_at),default=None)
    return {'lodging_sync_version':'-'.join(f'{state.source_version}:{state.calculated_version}' for state in states),
        'lodging_sync_count':sum(state.issue_count for state in states),'lodging_sync_current':current,'lodging_sync_busy':busy,
        'lodging_sync_calculated_at':calculated,'lodging_sync_url':reverse('personnel:lodging_sync')+'?'+urlencode(query,doseq=True),
        'lodging_sync_poll_url':reverse('personnel:lodging_sync_status')+'?'+urlencode(query,doseq=True),
        'lodging_sync_can_view':role_code(user) in lodging.READERS}


def launch_sync_worker(state):
    if 'test' in sys.argv or os.environ.get('LODGING_SYNC_INLINE')=='1':
        lodging_sync.calculate_state(state.pk);return
    runtime=Path(settings.CONFIG_DIR)/'.runtime'/'lodging-sync';runtime.mkdir(parents=True,exist_ok=True)
    command=[sys.executable,str(Path(settings.BASE_DIR)/'manage.py'),'process_lodging_sync',str(state.pk)]
    with (runtime/f'{state.pk}.log').open('ab') as output:
        subprocess.Popen(command,cwd=str(settings.BASE_DIR),env=os.environ.copy(),stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


@login_required
@require_http_methods(['GET','POST'])
def board(request):
    started=perf_counter();timings={}
    require_roles(request.user,lodging.READERS)
    camps=Camp.objects.filter(company=request.user.company)
    raw=request.GET.copy()
    if request.method=='POST':raw.update({k:request.POST[k] for k in ('camp',) if k in request.POST})
    if not raw.get('camp') and camps.exists():raw['camp']=str(camps.first().pk)
    selector=LodgingFilter(raw,company=request.user.company)
    if not selector.is_valid():return render(request,'personnel/lodging.html',{'selector':selector,'has_camps':camps.exists()})
    camp=selector.cleaned_data['camp'];day=timezone.localdate()
    # One operator edits the whole location; everybody else remains a live viewer.
    provided=request.POST.get('session') if request.method=='POST' else None
    try:presence_state,presence_session=touch_presence(request,camp,provided)
    except ValidationError as exc:raise Http404('; '.join(exc.messages))
    editable=presence_state['editor']
    ajax=request.headers.get('X-Requested-With')=='XMLHttpRequest'
    if request.method=='POST' and not editable:
        message=f'Только просмотр: местонахождение сейчас редактирует {presence_state["editor_name"] or "другой комендант"}.'
        if ajax:return JsonResponse({'error':message},status=409)
        messages.error(request,message)
        return redirect(board_url(camp,day))
    initial={'date':day,'session':presence_session,'kind':'CHECKIN'}
    if request.GET.get('employee','').isdigit():initial['employee']=int(request.GET['employee'])
    if request.GET.get('kind') in {'CHECKIN','MOVE','CHECKOUT','UPDATE'}:initial['kind']=request.GET['kind']
    form=LodgingMovementForm(request.POST if request.method=='POST' else None,company=request.user.company,camp=camp,day=day,user=request.user,initial=initial)
    if request.method=='POST':
        require_roles(request.user,lodging.OPERATORS)
        if form.is_valid():
            data=form.cleaned_data
            try:
                previous=lodging.present(request.user.company,data['date']).filter(employee=data['employee']).select_related('bed__building').first()
                stay=lodging.move(request.user,data['kind'],camp,data['date'],data['employee'],bed=data.get('bed') if data['kind'] in {'CHECKIN','MOVE'} else None,
                    session=data['session'],signed=data['signed'],keys_issued=data['keys_issued'],key_number=data['key_number'],pass_number=data['pass_number'],comment=data['comment'])
            except ValidationError as exc:form.add_error(None,exc)
            else:
                result_message=movement_success(data['kind'],data['employee'],camp,data['date'],previous.bed if previous else None,stay.bed if data['kind'] in {'CHECKIN','MOVE'} else None)
                if ajax:
                    stats,group_counts=aggregate_place_counts(request.user.company,camp)
                    affected={bed.building_id for bed in (previous.bed if previous else None,stay.bed if data['kind'] in {'CHECKIN','MOVE'} else None) if bed}
                    return measured(JsonResponse({'ok':True,'message_html':str(result_message),'affected_buildings':sorted(affected),
                        'kind':data['kind'],'employee':data['employee'].pk,
                        'stay':None if data['kind']=='CHECKOUT' else {'employee':data['employee'].pk,'bed':stay.bed_id,'keys_issued':stay.keys_issued,'key_number':stay.key_number,'pass_number':stay.pass_number,'comment':stay.comment},
                        'stats':{key:stats[key] for key in ('OCCUPIED','FREE','RESERVED','UNAVAILABLE')},
                        'groups':{str(pk):{key:value[key] for key in ('TOTAL','OCCUPIED','FREE','RESERVED','UNAVAILABLE')} for pk,value in group_counts.items()}}),'lodging save',started)
                messages.success(request,result_message)
                return redirect(board_url(camp,data['date']))
        if ajax:return JsonResponse({'error':'Исправьте данные формы.','errors':form.errors.get_json_data()},status=400)
    q=selector.cleaned_data['q'].casefold().split();state=selector.cleaned_data['state']
    building_ids=set(selector.cleaned_data['buildings'].values_list('pk',flat=True))
    bed_ids=set(selector.cleaned_data['beds'].values_list('pk',flat=True))
    timings['presence_form']=(perf_counter()-started)*1000
    rows=None
    if q or state or bed_ids or request.GET.get('export')=='xlsx':
        all_rows=place_rows(request.user.company,camp,day,bed_ids=bed_ids or None)
        if building_ids:all_rows=[row for row in all_rows if row['bed'].building_id in building_ids]
        stats=Counter(row['status'] for row in all_rows)
        rows=[r for r in all_rows if (not state or r['status']==state) and all(term in ' '.join([str(r['bed']),lodging.building_title(r['bed'].building),r['stay'].employee.display_name if r['stay'] else '',r['stay'].employee.employee_number if r['stay'] else '',r['organization']]).casefold() for term in q)]
        counts={}
        for row in rows:
            item=counts.setdefault(row['bed'].building_id,Counter());item[row['status']]+=1;item['TOTAL']+=1
    else:
        stats,counts=aggregate_place_counts(request.user.company,camp,building_ids or None)
    timings['places']=(perf_counter()-started)*1000-timings['presence_form']
    from .housing_views import workbook_response
    if request.GET.get('export')=='xlsx':
        values=[['Местонахождение','Вагон / корпус','Инвентарный номер','Сведения о вагоне','Тип','Комната','Место','Состояние','ФИО','Табельный номер','Пол','Группа должностей','Должность','Подразделение','Ключи','Номер ключа','Заселение','Плановая дата выселения','Выселение','Пропуск','Примечание','Пол вагона']]
        for row in rows:
            b=row['bed'];s=row['stay'];e=s.employee if s else None
            values.append([camp.name,b.building.name,b.building.inventory_number,b.building.description,b.building.kind,b.room,b.number,row['status_label'],e.display_name if e else '',e.employee_number if e else '',row['gender'],row['position_group'],row['position'],row['department'],('Да' if s.keys_issued else 'Нет') if s else '',s.key_number if s else '',s.arrived_on if s else '',s.planned_departure if s else '',s.departed_on if s else '',s.pass_number if s else '',s.comment if s else b.note,b.building.get_gender_display()])
        return workbook_response('lodging.xlsx','Проживание',values)
    buildings=LodgingBuilding.objects.filter(company=request.user.company,camp=camp)
    if building_ids:buildings=buildings.filter(pk__in=building_ids)
    filter_query={'camp':camp.pk,'date':str(day),'q':selector.cleaned_data['q'],'state':state,
        'buildings':sorted(building_ids),'beds':sorted(bed_ids)}
    groups=[{'building':b,'counts':dict(counts.get(b.pk,{})),'load_url':reverse('personnel:lodging_building_places',args=[b.pk])+'?'+urlencode(filter_query,doseq=True)} for b in buildings]
    if q or state or bed_ids:groups=[g for g in groups if g['counts'].get('TOTAL')]
    history_url=reverse('personnel:lodging_event_history')+'?'+urlencode({'camp':camp.pk,'date':str(day)})
    context={'groups':groups,'selector':selector,'has_camps':camps.exists(),'camp':camp,'day':day,'form':form,'stats':dict(stats),'editable':editable,'catalogue_editable':editable,'history_url':history_url,
        'presence_state':presence_state,'presence_session':presence_session,
        'quick_bed_form':QuickBedForm(company=request.user.company,camp=camp),
        'movement_open':request.method=='POST' or bool(request.GET.get('employee')),
        'roster_data':[{'employee':s.employee_id,'bed':s.bed_id,'keys_issued':s.keys_issued,'key_number':s.key_number,'pass_number':s.pass_number,'comment':s.comment} for s in lodging.present(request.user.company,day).filter(camp=camp)],
        **summary_context(request.user,camp,day)}
    timings['context']=(perf_counter()-started)*1000-sum(timings.values());response=render(request,'personnel/lodging.html',context)
    timings['render']=(perf_counter()-started)*1000-sum(timings.values());response['Server-Timing']=', '.join(f'{name};dur={value:.1f}' for name,value in timings.items())
    elapsed=(perf_counter()-started)*1000
    if elapsed>=1000:PERFORMANCE.warning('lodging board %.0f ms: %s',elapsed,{key:round(value) for key,value in timings.items()})
    return response


@login_required
@require_POST
def quick_bed(request):
    require_roles(request.user,lodging.OPERATORS)
    camp,day=parameters(request)
    state,_=touch_presence(request,camp,request.POST.get('presence_session'))
    if not state['editor']:return JsonResponse({'error':editor_error(state)},status=409)
    form=QuickBedForm(request.POST,company=request.user.company,camp=camp)
    if not form.is_valid():return JsonResponse({'errors':form.errors.get_json_data()},status=400)
    data=form.cleaned_data
    from core.fact_history import history_actor
    try:
        with transaction.atomic(),history_actor(request.user):
            if data.get('building'):
                building=get_object_or_404(LodgingBuilding.objects.select_for_update(),pk=data['building'].pk,company=request.user.company,camp=camp,is_active=True)
            else:
                building=LodgingBuilding(company=request.user.company,camp=camp,name=data['new_building_name'],description=data['new_building_description'],inventory_number=data['new_building_inventory_number'],gender=data['new_building_gender'])
                building.save()
            bed=LodgingBed(company=request.user.company,building=building,room=data['room'],number=data['number'],note=data['note'])
            bed.save()
    except ValidationError as exc:
        return JsonResponse({'error':'; '.join(exc.messages)},status=400)
    except IntegrityError:
        return JsonResponse({'error':'Такой вагон или место уже существует. Выберите его в списке либо укажите другой номер.'},status=409)
    load_url=reverse('personnel:lodging_building_places',args=[building.pk])+'?'+urlencode({'camp':camp.pk,'date':str(day)})
    return JsonResponse({'id':bed.pk,'label':lodging.bed_label(bed),'building_gender':bed.building.gender,
        'building':{'id':building.pk,'name':building.name,'description':building.description,'label':lodging.building_label(building),'gender':building.gender,
            'gender_label':building.get_gender_display(),'created':not bool(data.get('building')),'load_url':load_url}},status=201)


@login_required
@require_POST
def claims(request):
    require_roles(request.user,lodging.OPERATORS)
    try:
        data=json.loads(request.body)
        if not isinstance(data,dict):raise ValidationError('Некорректные данные.')
        if not str(data.get('camp','')).isdigit() or not str(data.get('employee','')).isdigit() or data.get('bed') and not str(data['bed']).isdigit():raise ValidationError('Выберите местонахождение, сотрудника и место.')
        camp=get_object_or_404(Camp,company=request.user.company,pk=data.get('camp'));day=date.fromisoformat(data.get('date',''))
        state,_=touch_presence(request,camp,data.get('session'))
        if not state['editor']:raise ValidationError(editor_error(state))
        employee=get_object_or_404(Employee,company=request.user.company,pk=data.get('employee'))
        bed=get_object_or_404(LodgingBed.objects.select_related('building'),company=request.user.company,pk=data['bed']) if data.get('bed') else None
        signed=lodging.claim(request.user,camp,day,employee,bed,data.get('session'),data.get('action','acquire'))
        return JsonResponse({'signed':signed,'owner':request.user.get_full_name() or request.user.username,'expires_seconds':int(lodging.TTL.total_seconds())})
    except (json.JSONDecodeError,ValidationError,ValueError,TypeError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректные данные.'},status=409)


@login_required
@require_POST
def presence(request):
    require_roles(request.user,lodging.READERS)
    try:
        data=json.loads(request.body)
        if not isinstance(data,dict) or not str(data.get('camp','')).isdigit():raise ValidationError('Некорректные данные присутствия.')
        camp=get_object_or_404(Camp,company=request.user.company,pk=data['camp'])
        return JsonResponse(lodging.presence(request.user,camp,data.get('session'),data.get('action','touch')))
    except (json.JSONDecodeError,ValidationError,ValueError,TypeError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректные данные.'},status=409)


@login_required
@require_http_methods(['GET','POST'])
def building_edit(request,pk=None):
    require_roles(request.user,lodging.OPERATORS)
    camp,day=parameters(request);instance=get_object_or_404(LodgingBuilding,company=request.user.company,camp=camp,pk=pk) if pk else LodgingBuilding(company=request.user.company,camp=camp)
    access=editor_presence(request,camp,request.POST.get('presence_session'))
    previous=instance.updated_at.isoformat() if pk else ''
    form=BuildingForm(request.POST if request.method=='POST' else None,instance=instance)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if pk:
                    existing=LodgingBuilding.objects.select_for_update().get(pk=pk,company=request.user.company)
                    if request.POST.get('version')!=existing.updated_at.isoformat():raise ValidationError('Вагон уже изменён. Откройте форму заново.')
                row=form.save()
                if not pk:
                    for n in range(1,(form.cleaned_data.get('places') or 0)+1):LodgingBed.objects.create(company=request.user.company,building=row,number=str(n))
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'Вагон и места сохранены.');return redirect(board_url(camp,day))
    return render(request,'personnel/lodging_catalogue_form.html',{'form':form,'camp':camp,'day':day,'version':request.POST.get('version',previous) if request.method=='POST' else previous,'title':'Изменить вагон / корпус' if pk else 'Добавить вагон / корпус',**access})


@login_required
@require_http_methods(['GET','POST'])
def bed_edit(request,building_pk,pk=None):
    require_roles(request.user,lodging.OPERATORS)
    building=get_object_or_404(LodgingBuilding,company=request.user.company,pk=building_pk);camp=building.camp
    access=editor_presence(request,camp,request.POST.get('presence_session'))
    try:day=date.fromisoformat(request.POST.get('date',request.GET.get('date','')))
    except ValueError:day=timezone.localdate()
    instance=get_object_or_404(LodgingBed,company=request.user.company,building=building,pk=pk) if pk else LodgingBed(company=request.user.company,building=building)
    previous=instance.updated_at.isoformat() if pk else '';form=BedForm(request.POST if request.method=='POST' else None,instance=instance)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if pk:
                    current=LodgingBed.objects.select_for_update().get(pk=pk,company=request.user.company)
                    if request.POST.get('version')!=current.updated_at.isoformat():raise ValidationError('Место уже изменено. Откройте форму заново.')
                    if form.cleaned_data['state']!='AVAILABLE' and lodging.present(request.user.company,day).filter(bed=current).exists():raise ValidationError('Место занято. Сначала оформите выселение или переселение.')
                form.save()
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'Место сохранено.');return redirect(board_url(camp,day))
    return render(request,'personnel/lodging_catalogue_form.html',{'form':form,'camp':camp,'day':day,'version':request.POST.get('version',previous) if request.method=='POST' else previous,'title':('Изменить место · ' if pk else 'Добавить место · ')+str(building),**access})


@login_required
@require_GET
def sync_status(request):
    require_roles(request.user,lodging.READERS);camp,day=parameters(request)
    selected=sync_camps(request,camp)
    states=[lodging_sync.state_for(request.user.company,item,day) for item in selected]
    if len(states)==1:return JsonResponse(lodging_sync.state_payload(states[0]))
    return JsonResponse({'count':sum(state.issue_count for state in states),'version':'-'.join(f'{state.source_version}:{state.calculated_version}' for state in states),
        'current':all(state.current for state in states),'dirty':any(not state.current for state in states),'busy':any(state.status in {'QUEUED','RUNNING'} for state in states)})


def sync_camps(request,camp):
    ids=request.GET.getlist('camps') or [str(camp.pk)]
    if any(not str(pk).isdigit() for pk in ids):raise Http404('Некорректный список местонахождений.')
    selected=list(Camp.objects.filter(company=request.user.company,pk__in=ids))
    if len(selected)!=len(set(ids)):raise Http404('Выберите местонахождение своей компании.')
    return selected


@login_required
@require_http_methods(['GET','POST'])
def sync(request):
    require_roles(request.user,lodging.READERS);camp,day=parameters(request);error=''
    selected=sync_camps(request,camp)
    if request.method=='POST':
        try:
            if request.POST.get('action')=='arrival':
                actual=date.fromisoformat(request.POST.get('actual_date',''))
                lodging_sync.confirm_arrival(request.user,camp,day,request.POST.get('signed'),actual,request.POST.get('reason',''))
                messages.success(request,'Заезд подтверждён. Сверка обновлена.')
            elif request.POST.get('action')=='production_arrival':
                lodging.require_editor(request.user,camp)
                if not str(request.POST.get('target_camp','')).isdigit():raise ValidationError('Выберите фактическое местонахождение.')
                target=get_object_or_404(Camp,company=request.user.company,is_active=True,pk=request.POST['target_camp'])
                actual=date.fromisoformat(request.POST.get('actual_date',''))
                lodging_sync.confirm_production_arrival(request.user,camp,day,request.POST.get('signed'),target,actual)
                messages.success(request,'Заезд оформлен. Производственный факт сохранён без изменений.')
            elif request.POST.get('action')=='explain':
                lodging_sync.explain(request.user,camp,day,request.POST.get('signed'),request.POST.get('reason',''));messages.success(request,'Объяснение сохранено в истории.')
            else:raise ValidationError('Выберите действие сверки.')
        except (ValidationError,ValueError) as exc:error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Укажите дату заезда.'
        else:return redirect(reverse('personnel:lodging_sync')+'?'+urlencode({'camp':camp.pk,'date':str(day)}))
    # The commandant works with one location. Multi-location HR links keep the
    # old direct calculation until their snapshots are opened separately.
    state=None
    if len(selected)==1:
        state=lodging_sync.state_for(request.user.company,camp,day)
        if request.GET.get('refresh')=='1':
            type(state).objects.filter(pk=state.pk).update(source_version=state.source_version+1);state.refresh_from_db()
        if not state.current and state.status not in {'QUEUED','RUNNING'}:
            state,started=lodging_sync.enqueue(request.user,camp,day)
            if started:
                try:launch_sync_worker(state)
                except OSError as exc:
                    type(state).objects.filter(pk=state.pk).update(status='ERROR',stage='Не удалось запустить расчёт',error=str(exc));state.refresh_from_db()
                else:state.refresh_from_db()
            else:state.refresh_from_db()
        else:state.refresh_from_db()
        rows=lodging_sync.snapshot_rows(state,include_resolved=request.GET.get('resolved')=='1') if state.status=='DONE' else []
    else:
        rows=lodging_sync.issues(request.user.company,selected,day,include_resolved=request.GET.get('resolved')=='1')
    for row in rows:row['signed']=lodging_sync.signed_issue(request.user,row)
    return render(request,'personnel/lodging_sync.html',{'camp':camp,'day':day,'issues':rows,'error':error,'sync_state':state,
        'sync_status_url':reverse('personnel:lodging_sync_status')+'?'+urlencode({'camp':camp.pk,'date':str(day)}),
        'arrival_camps':Camp.objects.filter(company=request.user.company,is_active=True),'can_explain':role_code(request.user) in lodging.OPERATORS|lodging_sync.HR_OPERATORS and can_edit_fact_day(request.user,day),
        'can_confirm':role_code(request.user) in lodging_sync.HR_OPERATORS and can_edit_fact_day(request.user,day),'can_lodge':lodging.editor_state(request.user,camp)[0],'can_hr':role_code(request.user) in lodging_sync.HR_OPERATORS,
        **summary_context(request.user,camp,day)})


@login_required
@require_http_methods(['GET','POST'])
def stay_edit(request,pk):
    require_roles(request.user,lodging.OPERATORS)
    stay=get_object_or_404(LodgingStay.objects.select_related('bed__building','employee','camp'),company=request.user.company,pk=pk)
    access=editor_presence(request,stay.camp,request.POST.get('presence_session'))
    # Form validation uses a separate model instance; service rereads the locked original.
    form=CorrectionForm(request.POST if request.method=='POST' else None,instance=stay,initial={'version':stay.updated_at.isoformat()})
    if request.method=='POST' and form.is_valid():
        try:
            if request.POST.get('action')=='cancel':lodging.cancel(request.user,stay,form.cleaned_data['reason'],form.cleaned_data['version'])
            else:lodging.correct(request.user,stay,form.cleaned_data,form.cleaned_data['version'],form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'Изменение сохранено с историей.');return redirect(board_url(stay.camp,timezone.localdate())+'#history')
    return render(request,'personnel/lodging_correction.html',{'form':form,'stay':stay,**access})
