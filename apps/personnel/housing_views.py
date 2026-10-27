from calendar import monthrange
from datetime import date,timedelta
from io import BytesIO
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
from .models import Camp,CampObjectLink,CampStay,HousingEvent,Person,Board,StaffingGroup,StaffingSlot
from .forms import CampForm,MovementForm,PersonScheduleForm
from . import housing
from .services import calendar_for,departure_days


def selected_day(request):
    try:return forms.DateField().clean((request.POST if request.method=='POST' else request.GET).get('date') or timezone.localdate().isoformat())
    except ValidationError:return timezone.localdate()


def camp_url(camp,day):return reverse('personnel:housing')+f'?camp={camp.pk}&date={day.isoformat()}'


@login_required
def camps(request):
    qs=scoped(request,Camp).prefetch_related('objects_served')
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
    stays=housing.present_stays(company,day).filter(camp=camp).select_related('person__employee__position','person__schedule_slot__pattern','person__schedule_slot__group__board','recorded_by')
    if query:stays=stays.filter(Q(person__full_name__icontains=query)|Q(person__employee_number__icontains=query)|Q(person__employee__position__name__icontains=query))
    if position:stays=stays.filter(person__employee__position_id=position)
    days=[day.replace(day=n) for n in range(1,monthrange(day.year,day.month)[1]+1)]
    rows=[]
    for stay in stays.order_by('person__employee__position__name','person__full_name','pk'):
        person=stay.person;slot=housing.schedule_slot(person);calendar=calendar_for(slot) if slot else {}
        planned_departure=next(iter(departure_days(calendar,day)),'')
        rows.append({'stay':stay,'person':person,'position':person.employee.position if person.employee_id else None,'slot':slot,
            'codes':[calendar.get(d.isoformat(),'') for d in days],'today_code':calendar.get(day.isoformat(),''),
            'planned_departure':planned_departure,'planned_departure_display':date.fromisoformat(planned_departure) if planned_departure else None,
            'position_label':str(person.employee.position) if person.employee_id and person.employee.position_id else slot.group.position if slot else 'Должность не указана'})
    rows.sort(key=lambda row:(row['position_label'].casefold(),row['person'].full_name.casefold()))
    return rows,days


@login_required
@require_http_methods(['GET','POST'])
def workspace(request):
    access(request);day=selected_day(request)
    camp_id=(request.POST if request.method=='POST' else request.GET).get('camp')
    if camp_id and not str(camp_id).isdigit():raise Http404('ВЖГ не найден.')
    camps=scoped(request,Camp).order_by('name')
    camp=get_object_or_404(camps,pk=camp_id) if camp_id else camps.first()
    if camp is None:return render(request,'personnel/housing.html',{'camps':camps,'day':day})
    if request.method=='POST':
        try:require_fact_day(request.user,day)
        except ValidationError as exc:raise PermissionDenied('; '.join(exc.messages))
    kind=request.GET.get('action','ARRIVAL');kind=kind if kind in {'ARRIVAL','DEPARTURE','TRANSFER'} else 'ARRIVAL'
    form=MovementForm(request.POST if request.method=='POST' else None,company=request.user.company,camp=camp,day=day,
        initial={'kind':kind,'date':day,'snapshot':housing.movement_snapshot(request.user),'people':[request.GET['person']] if request.GET.get('person','').isdigit() else []})
    if request.method=='POST' and form.is_valid():
        try:
            count=housing.move(request.user,form.cleaned_data['kind'],camp,form.cleaned_data['date'],form.cleaned_data['people'],
                employees=form.cleaned_data['employees'],target=form.cleaned_data.get('target'),comment=form.cleaned_data['comment'],snapshot=form.cleaned_data['snapshot'])
        except (ValidationError,IntegrityError) as exc:form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Размещение изменилось одновременно с вашим вводом. Обновите список.')
        else:
            messages.success(request,f'Перемещение сохранено. Сотрудников: {count}.');return redirect(camp_url(camp,form.cleaned_data['date']))
    query=request.GET.get('q','').strip();position=request.GET.get('position','')
    if position and not position.isdigit():position=''
    rows,days=roster_rows(request.user.company,camp,day,query,position)
    from apps.resources.models import Position
    events=HousingEvent.objects.filter(company=request.user.company).filter(Q(from_camp=camp)|Q(to_camp=camp)).select_related('person','from_camp','to_camp','recorded_by')[:50]
    count=housing.present_stays(request.user.company,day).filter(camp=camp).count()
    return render(request,'personnel/housing.html',{'camp':camp,'camps':camps,'day':day,'rows':rows,'dates':days,'form':form,'query':query,
        'positions':Position.objects.filter(company=request.user.company,employees__personnel_card__stays__in=housing.present_stays(request.user.company,day).filter(camp=camp)).distinct().order_by('name'),
        'selected_position':position,'events':events,'total':count,'objects':camp.objects_served.filter(company=request.user.company),
        'unknown_count':Person.objects.filter(company=request.user.company,stays__isnull=True).count(),
        'unlinked_count':Person.objects.filter(company=request.user.company,employee=None).count(),**window_context(request.user,day)})


@login_required
def history(request,pk):
    person=get_object_or_404(scoped(request,Person),pk=pk)
    stays=person.stays.select_related('camp','recorded_by','cancelled_by')
    for stay in stays:stay.can_cancel=can_edit_fact_day(request.user,stay.arrived_on)
    events=person.housing_events.select_related('from_camp','to_camp','recorded_by')
    return render(request,'personnel/housing_history.html',{'person':person,'stays':stays,'events':events})


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
    form=PersonScheduleForm(request.POST if request.method=='POST' else None,person=person)
    if request.method=='GET' and request.GET.get('slot','').isdigit():
        selected=person.assignments.filter(company=person.company,pk=request.GET['slot']).first()
        if selected:form.initial.update(slot=selected.pk,pattern=selected.pattern_id)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            from apps.accounts.models import Company
            from django.db.models import F
            Company.objects.filter(pk=request.user.company_id).update(name=F('name'))
            person=Person.objects.select_for_update().get(pk=person.pk,company=request.user.company)
            slot=form.cleaned_data.get('slot');pattern=form.cleaned_data.get('pattern')
            if slot:
                slot=StaffingSlot.objects.select_for_update().get(pk=slot.pk,person=person,company=person.company)
                if pattern and pattern.board_id!=slot.group.board_id:
                    form.add_error('pattern','График должен принадлежать таблице выбранной строки. Выберите другую строку или очистите выбор строки.')
            if not form.errors:
                try:housing.check_schedule(person,form.cleaned_data['version'],slot)
                except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
            if not form.errors:
                if not slot:
                    board=pattern.board if pattern else Board.objects.filter(company=person.company,name='Графики сотрудников ВЖГ',import_digest='').order_by('pk').first()
                    if board is None:board=Board.objects.create(company=person.company,name='Графики сотрудников ВЖГ')
                    position=str(person.employee.position) if person.employee_id and person.employee.position_id else 'Без должности'
                    group=board.groups.filter(position=position).first() or StaffingGroup.objects.create(company=person.company,board=board,position=position)
                    slot=StaffingSlot(company=person.company,group=group,person=person)
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
    return render(request,'personnel/form.html',{'form':form,'title':'График: '+person.full_name})


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
    camp_id=request.GET.get('camp','')
    if not camp_id.isdigit():raise Http404('ВЖГ не найден.')
    camp=get_object_or_404(scoped(request,Camp),pk=camp_id);day=selected_day(request)
    rows,days=roster_rows(request.user.company,camp,day,request.GET.get('q','').strip(),request.GET.get('position','') if request.GET.get('position','').isdigit() else '')
    return workbook_response('camp-roster.xlsx','Состав ВЖГ',[['ВЖГ','Дата','ФИО','Табельный номер','Должность','Заезд','Выезд по графику','Фактический выезд',*[d.strftime('%d.%m.%Y') for d in days]],*[[camp.name,day.isoformat(),r['person'].full_name,r['person'].employee_number,r['position_label'],r['stay'].arrived_on.isoformat(),r['planned_departure'],r['stay'].departed_on.isoformat() if r['stay'].departed_on else '',*r['codes']] for r in rows]])
