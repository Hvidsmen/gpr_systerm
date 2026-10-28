import json
from collections import Counter
from datetime import date
from uuid import uuid4
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction,IntegrityError
from django.http import JsonResponse,Http404
from django.shortcuts import get_object_or_404,render,redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET,require_POST,require_http_methods
from core.permissions import require_roles,role_code
from core.fact_edit_window import can_edit_fact_day,window_context
from apps.resources.models import Employee
from .models import Camp,LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl
from . import lodging,lodging_sync
from .lodging_forms import LodgingFilter,LodgingMovementForm,BuildingForm,BedForm,CorrectionForm,QuickBedForm


def parameters(request):
    source=request.POST if request.method=='POST' else request.GET
    if not str(source.get('camp','')).isdigit():raise Http404('Укажите ВЖГ.')
    camp=get_object_or_404(Camp,company=request.user.company,pk=source.get('camp'))
    try:day=date.fromisoformat(source.get('date',''))
    except (ValueError,TypeError):day=timezone.localdate()
    return camp,day


def board_url(camp,day):return reverse('personnel:lodging')+'?'+urlencode({'camp':camp.pk,'date':str(day)})


def employee_company(employee):return employee.zup_organisation or str(employee.company)


def place_rows(company,camp,day):
    occupied={stay.bed_id:stay for stay in lodging.present(company,day).filter(camp=camp).select_related('employee__position','employee__company','bed__building','recorded_by')}
    leases={row.resource_id:row for row in LodgingControl.objects.filter(company=company,resource='bed',expires_at__gt=timezone.now()).select_related('locked_by')}
    rows=[]
    for bed in LodgingBed.objects.filter(company=company,building__camp=camp).select_related('building'):
        stay=occupied.get(bed.pk);status='OCCUPIED' if stay else 'UNAVAILABLE' if not bed.building.is_active else 'FREE' if bed.state=='AVAILABLE' else bed.state
        rows.append({'bed':bed,'stay':stay,'status':status,'status_label':{'OCCUPIED':'Занято','FREE':'Свободно','RESERVED':'Зарезервировано','UNAVAILABLE':'Недоступно'}[status],
            'organization':(stay.organization_snapshot or employee_company(stay.employee)) if stay else '', 'lease':leases.get(bed.pk),
            'position':(stay.position_snapshot if stay.organization_snapshot else stay.employee.display_position) if stay else '',
            'department':(stay.department_snapshot if stay.organization_snapshot else stay.employee.display_department) if stay else '',
            'overdue':bool(stay and stay.planned_departure and stay.planned_departure<day)})
    return rows


def summary_context(user,camp,day,*,camps=None):
    selected=list(camps) if camps is not None else [camp]
    query={'camp':camp.pk,'date':str(day)}
    if len(selected)>1:query['camps']=[c.pk for c in selected]
    rows=lodging_sync.issues(user.company,selected,day)
    return {'lodging_sync_version':lodging_sync.digest(rows),'lodging_sync_count':len(rows),'lodging_sync_url':reverse('personnel:lodging_sync')+'?'+urlencode(query,doseq=True),
        'lodging_sync_poll_url':reverse('personnel:lodging_sync_status')+'?'+urlencode(query,doseq=True),
        'lodging_sync_can_view':role_code(user) in lodging.READERS}


@login_required
@require_http_methods(['GET','POST'])
def board(request):
    require_roles(request.user,lodging.READERS)
    camps=Camp.objects.filter(company=request.user.company)
    raw=request.GET.copy()
    if request.method=='POST':raw.update({k:request.POST[k] for k in ('camp','date') if k in request.POST})
    raw.setdefault('date',str(timezone.localdate()))
    if not raw.get('camp') and camps.exists():raw['camp']=str(camps.first().pk)
    selector=LodgingFilter(raw,company=request.user.company)
    if not selector.is_valid():return render(request,'personnel/lodging.html',{'selector':selector,'has_camps':camps.exists()})
    camp=selector.cleaned_data['camp'];day=selector.cleaned_data['date']
    editable=role_code(request.user) in lodging.OPERATORS and can_edit_fact_day(request.user,day)
    initial={'date':day,'session':uuid4(),'kind':'CHECKIN'}
    if request.GET.get('employee','').isdigit():initial['employee']=int(request.GET['employee'])
    if request.GET.get('kind') in {'CHECKIN','MOVE','CHECKOUT','UPDATE'}:initial['kind']=request.GET['kind']
    form=LodgingMovementForm(request.POST if request.method=='POST' else None,company=request.user.company,camp=camp,day=day,initial=initial)
    if request.method=='POST':
        require_roles(request.user,lodging.OPERATORS)
        if form.is_valid():
            data=form.cleaned_data
            try:
                lodging.move(request.user,data['kind'],camp,data['date'],data['employee'],bed=data.get('bed') if data['kind'] in {'CHECKIN','MOVE'} else None,
                    session=data['session'],signed=data['signed'],planned_departure=data.get('planned_departure'),keys_issued=data['keys_issued'],pass_number=data['pass_number'],comment=data['comment'])
            except ValidationError as exc:form.add_error(None,exc)
            else:messages.success(request,'Проживание сохранено. Сверка с перевахтой обновлена.');return redirect(board_url(camp,data['date']))
    all_rows=place_rows(request.user.company,camp,day);stats=Counter(row['status'] for row in all_rows)
    organizations=Counter(row['organization'] for row in all_rows if row['stay'])
    q=selector.cleaned_data['q'].casefold().split();state=selector.cleaned_data['state']
    rows=[r for r in all_rows if (not state or r['status']==state) and all(term in ' '.join([str(r['bed']),r['stay'].employee.display_name if r['stay'] else '',r['stay'].employee.employee_number if r['stay'] else '',r['organization']]).casefold() for term in q)]
    from .housing_views import workbook_response
    if request.GET.get('export')=='xlsx':
        values=[['ВЖГ','Вагон / корпус','Инвентарный номер','Тип','Комната','Место','Состояние','ФИО','Табельный номер','Организация','Должность','Подразделение','Ключи','Заселение','Плановое освобождение','Выселение','Пропуск','Примечание']]
        for row in rows:
            b=row['bed'];s=row['stay'];e=s.employee if s else None
            values.append([camp.name,b.building.name,b.building.inventory_number,b.building.kind,b.room,b.number,row['status_label'],e.display_name if e else '',e.employee_number if e else '',row['organization'],row['position'],row['department'],('Да' if s.keys_issued else 'Нет') if s else '',s.arrived_on if s else '',s.planned_departure if s else '',s.departed_on if s else '',s.pass_number if s else '',s.comment if s else b.note])
        return workbook_response('lodging.xlsx','Проживание',values)
    events=LodgingEvent.objects.filter(company=request.user.company,camp=camp,date__lte=day).select_related('employee','from_bed__building','to_bed__building','recorded_by','stay')[:100]
    groups=[{'building':b,'places':[r for r in rows if r['bed'].building_id==b.pk]} for b in LodgingBuilding.objects.filter(company=request.user.company,camp=camp)]
    if q or state:groups=[g for g in groups if g['places']]
    context={'groups':groups,'selector':selector,'has_camps':camps.exists(),'camp':camp,'day':day,'form':form,'rows':rows,'stats':dict(stats),'organizations':sorted(organizations.items()),'editable':editable,'catalogue_editable':role_code(request.user) in lodging.OPERATORS,'events':events,
        'quick_bed_form':QuickBedForm(company=request.user.company,camp=camp),
        'movement_open':request.method=='POST' or bool(request.GET.get('employee')),
        'roster_data':[{'employee':s.employee_id,'bed':s.bed_id,'planned_departure':str(s.planned_departure or ''),'keys_issued':s.keys_issued,'pass_number':s.pass_number,'comment':s.comment} for s in lodging.present(request.user.company,day).filter(camp=camp)],
        **summary_context(request.user,camp,day),**window_context(request.user,day)}
    return render(request,'personnel/lodging.html',context)


@login_required
@require_POST
def quick_bed(request):
    require_roles(request.user,lodging.OPERATORS)
    camp,day=parameters(request)
    form=QuickBedForm(request.POST,company=request.user.company,camp=camp)
    if not form.is_valid():return JsonResponse({'errors':form.errors.get_json_data()},status=400)
    data=form.cleaned_data
    from core.fact_history import history_actor
    try:
        with transaction.atomic(),history_actor(request.user):
            lodging.mutex(request.user)
            if data.get('building'):
                building=get_object_or_404(LodgingBuilding.objects.select_for_update(),pk=data['building'].pk,company=request.user.company,camp=camp,is_active=True)
            else:
                building=LodgingBuilding(company=request.user.company,camp=camp,name=data['new_building_name'],inventory_number=data['new_building_inventory_number'])
                building.save()
            bed=LodgingBed(company=request.user.company,building=building,room=data['room'],number=data['number'],note=data['note'])
            bed.save()
    except ValidationError as exc:
        return JsonResponse({'error':'; '.join(exc.messages)},status=400)
    except IntegrityError:
        return JsonResponse({'error':'Такой вагон или место уже существует. Выберите его в списке либо укажите другой номер.'},status=409)
    return JsonResponse({'id':bed.pk,'label':str(bed)+' — Свободно','building':{'id':building.pk,'name':building.name}},status=201)


@login_required
@require_POST
def claims(request):
    require_roles(request.user,lodging.OPERATORS)
    try:
        data=json.loads(request.body)
        if not isinstance(data,dict):raise ValidationError('Некорректные данные.')
        if not str(data.get('camp','')).isdigit() or not str(data.get('employee','')).isdigit() or data.get('bed') and not str(data['bed']).isdigit():raise ValidationError('Выберите ВЖГ, сотрудника и место.')
        camp=get_object_or_404(Camp,company=request.user.company,pk=data.get('camp'));day=date.fromisoformat(data.get('date',''))
        employee=get_object_or_404(Employee,company=request.user.company,pk=data.get('employee'))
        bed=get_object_or_404(LodgingBed.objects.select_related('building'),company=request.user.company,pk=data['bed']) if data.get('bed') else None
        signed=lodging.claim(request.user,camp,day,employee,bed,data.get('session'),data.get('action','acquire'))
        return JsonResponse({'signed':signed,'owner':request.user.get_full_name() or request.user.username,'expires_seconds':int(lodging.TTL.total_seconds())})
    except (json.JSONDecodeError,ValidationError,ValueError,TypeError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Некорректные данные.'},status=409)


@login_required
@require_http_methods(['GET','POST'])
def building_edit(request,pk=None):
    require_roles(request.user,lodging.OPERATORS)
    camp,day=parameters(request);instance=get_object_or_404(LodgingBuilding,company=request.user.company,camp=camp,pk=pk) if pk else LodgingBuilding(company=request.user.company,camp=camp)
    previous=instance.updated_at.isoformat() if pk else ''
    form=BuildingForm(request.POST if request.method=='POST' else None,instance=instance)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                lodging.mutex(request.user)
                if pk:
                    existing=LodgingBuilding.objects.select_for_update().get(pk=pk,company=request.user.company)
                    if request.POST.get('version')!=existing.updated_at.isoformat():raise ValidationError('Вагон уже изменён. Откройте форму заново.')
                row=form.save()
                if not pk:
                    for n in range(1,(form.cleaned_data.get('places') or 0)+1):LodgingBed.objects.create(company=request.user.company,building=row,number=str(n))
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'Вагон и места сохранены.');return redirect(board_url(camp,day))
    return render(request,'personnel/lodging_catalogue_form.html',{'form':form,'camp':camp,'day':day,'version':request.POST.get('version',previous) if request.method=='POST' else previous,'title':'Изменить вагон / корпус' if pk else 'Добавить вагон / корпус'})


@login_required
@require_http_methods(['GET','POST'])
def bed_edit(request,building_pk,pk=None):
    require_roles(request.user,lodging.OPERATORS)
    building=get_object_or_404(LodgingBuilding,company=request.user.company,pk=building_pk);camp=building.camp
    try:day=date.fromisoformat(request.POST.get('date',request.GET.get('date','')))
    except ValueError:day=timezone.localdate()
    instance=get_object_or_404(LodgingBed,company=request.user.company,building=building,pk=pk) if pk else LodgingBed(company=request.user.company,building=building)
    previous=instance.updated_at.isoformat() if pk else '';form=BedForm(request.POST if request.method=='POST' else None,instance=instance)
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                lodging.mutex(request.user)
                if pk:
                    current=LodgingBed.objects.select_for_update().get(pk=pk,company=request.user.company)
                    if request.POST.get('version')!=current.updated_at.isoformat():raise ValidationError('Место уже изменено. Откройте форму заново.')
                    lease=lodging.controls(request.user,[('bed',pk)])[0];lodging.locked_elsewhere(lease,request.user,None)
                    if form.cleaned_data['state']!='AVAILABLE' and lodging.present(request.user.company,day).filter(bed=current).exists():raise ValidationError('Место занято. Сначала оформите выселение или переселение.')
                form.save()
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'Место сохранено.');return redirect(board_url(camp,day))
    return render(request,'personnel/lodging_catalogue_form.html',{'form':form,'camp':camp,'day':day,'version':request.POST.get('version',previous) if request.method=='POST' else previous,'title':('Изменить место · ' if pk else 'Добавить место · ')+str(building)})


@login_required
@require_GET
def sync_status(request):
    require_roles(request.user,lodging.READERS);camp,day=parameters(request)
    selected=sync_camps(request,camp)
    rows=lodging_sync.issues(request.user.company,selected,day)
    return JsonResponse({'count':len(rows),'version':lodging_sync.digest(rows)})


def sync_camps(request,camp):
    ids=request.GET.getlist('camps') or [str(camp.pk)]
    if any(not str(pk).isdigit() for pk in ids):raise Http404('Некорректный список ВЖГ.')
    selected=list(Camp.objects.filter(company=request.user.company,pk__in=ids))
    if len(selected)!=len(set(ids)):raise Http404('Выберите ВЖГ своей компании.')
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
                if not str(request.POST.get('target_camp','')).isdigit():raise ValidationError('Выберите фактический ВЖГ.')
                target=get_object_or_404(Camp,company=request.user.company,is_active=True,pk=request.POST['target_camp'])
                actual=date.fromisoformat(request.POST.get('actual_date',''))
                lodging_sync.confirm_production_arrival(request.user,camp,day,request.POST.get('signed'),target,actual)
                messages.success(request,'Заезд оформлен. Производственный факт сохранён без изменений.')
            elif request.POST.get('action')=='explain':
                lodging_sync.explain(request.user,camp,day,request.POST.get('signed'),request.POST.get('reason',''));messages.success(request,'Объяснение сохранено в истории.')
            else:raise ValidationError('Выберите действие сверки.')
        except (ValidationError,ValueError) as exc:error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Укажите дату заезда.'
        else:return redirect(reverse('personnel:lodging_sync')+'?'+urlencode({'camp':camp.pk,'date':str(day)}))
    rows=lodging_sync.issues(request.user.company,selected,day,include_resolved=request.GET.get('resolved')=='1')
    for row in rows:row['signed']=lodging_sync.signed_issue(request.user,row)
    return render(request,'personnel/lodging_sync.html',{'camp':camp,'day':day,'issues':rows,'error':error,'arrival_camps':Camp.objects.filter(company=request.user.company,is_active=True),'can_explain':role_code(request.user) in lodging.OPERATORS|lodging_sync.HR_OPERATORS and can_edit_fact_day(request.user,day),
        'can_confirm':role_code(request.user) in lodging_sync.HR_OPERATORS and can_edit_fact_day(request.user,day),'can_lodge':role_code(request.user) in lodging.OPERATORS and can_edit_fact_day(request.user,day),'can_hr':role_code(request.user) in lodging_sync.HR_OPERATORS,
        **summary_context(request.user,camp,day)})


@login_required
@require_http_methods(['GET','POST'])
def stay_edit(request,pk):
    require_roles(request.user,lodging.OPERATORS)
    stay=get_object_or_404(LodgingStay.objects.select_related('bed__building','employee','camp'),company=request.user.company,pk=pk)
    # Form validation uses a separate model instance; service rereads the locked original.
    form=CorrectionForm(request.POST if request.method=='POST' else None,instance=stay,initial={'version':stay.updated_at.isoformat()})
    if request.method=='POST' and form.is_valid():
        try:
            if request.POST.get('action')=='cancel':lodging.cancel(request.user,stay,form.cleaned_data['reason'],form.cleaned_data['version'])
            else:lodging.correct(request.user,stay,form.cleaned_data,form.cleaned_data['version'],form.cleaned_data['reason'])
        except ValidationError as exc:form.add_error(None,exc)
        else:messages.success(request,'Изменение сохранено с историей.');return redirect(board_url(stay.camp,timezone.localdate())+'#history')
    return render(request,'personnel/lodging_correction.html',{'form':form,'stay':stay})
