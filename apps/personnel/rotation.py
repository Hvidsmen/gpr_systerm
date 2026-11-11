"""Rotation includes people at work, people on rest, and vacant staffing slots.
Actual residence and scheduled work are separate dated indicators.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date,timedelta
from django import forms
from django.db.models import Q
from apps.projects.models import ConstructionObject
from apps.resources.models import Position
from .models import Camp,CampStay,StaffingSlot
from . import housing
from .services import calendar_for,departure_days,is_work

STATUS_HELP={
    '':'Все сотрудники и вакансии, подходящие под остальные фильтры.',
    'DEPARTURE_OVERDUE':'Плановая дата выезда прошла, сотрудник ещё числится на вахте. Проверьте и оформите фактический выезд или уточните график.',
    'DEPARTURE_PENDING':'Сотрудник на вахте; выезд по графику сегодня или в ближайшие 14 дней включительно.',
    'ARRIVAL_OVERDUE':'Плановая дата возвращения прошла, а сотрудник пока на МО или со статусом, который нужно уточнить. Фактический заезд не подтверждён.',
    'ARRIVING':'На выбранную дату указан фактический или исходный заезд, запланировано возвращение либо в графике стоит входящий день пути.',
    'DEPARTING':'На выбранную дату указан фактический или исходный выезд либо выезд по графику. Включает дни пути на выезд и переводы из выбранного ВЖГ.',
    'MV':'Сотрудник числится на вахте по фактическим перемещениям; если их нет — по начальным данным. Это не означает, что сегодня у него рабочий день.',
    'MO':'Сотрудник на межвахтовом отдыхе по фактическому выезду; если перемещений нет — по начальным данным.',
    'TRAVEL':'В индивидуальном графике на выбранную дату стоит ДП — день пути. Направление поездки может быть на вахту или с вахты.',
    'VACANCY':'Штатная строка без назначенного сотрудника. Это свободная позиция из таблицы, а не рассчитанный дефицит по СМГ или ГПР.',
    'WORKING':'В индивидуальном графике на выбранную дату стоят рабочие часы больше нуля или МВ. Это рабочий день по графику, а не подтверждённый производственный факт.',
    'UNKNOWN':'По фактическим перемещениям и начальным данным нельзя определить, находится сотрудник на вахте или на МО. Уточните исходный статус или оформите перемещение.',
}


class RotationStatusSelect(forms.Select):
    def create_option(self,name,value,label,selected,index,subindex=None,attrs=None):
        option=super().create_option(name,value,label,selected,index,subindex,attrs)
        option['attrs']['title']=STATUS_HELP.get(str(value),'')
        option['attrs']['aria-description']=option['attrs']['title']
        return option


class RotationFilter(forms.Form):
    construction_object=forms.ModelChoiceField(label='Проект',queryset=ConstructionObject.objects.none(),required=True,empty_label=None)
    camps=forms.ModelMultipleChoiceField(label='ВЖГ',queryset=Camp.objects.none(),required=True,
        widget=forms.SelectMultiple(attrs={'size':6,'data-personnel-searchable':''}))
    date=forms.DateField(label='На дату',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    q=forms.CharField(label='Сотрудник',required=False,widget=forms.TextInput(attrs={'placeholder':'ФИО, должность или табельный номер'}))
    position=forms.ModelChoiceField(label='Должность',queryset=Position.objects.none(),required=False,empty_label='Все должности')
    state=forms.ChoiceField(label='Статус',required=False,widget=RotationStatusSelect,choices=[('','Все'),('DEPARTURE_OVERDUE','Выезд просрочен'),('DEPARTURE_PENDING','Ожидает выезда'),('ARRIVAL_OVERDUE','Заезд просрочен'),('ARRIVING','Заезжают'),('DEPARTING','Выезжают'),('MV','На вахте'),('MO','Межвахтовый отдых'),('TRAVEL','В пути'),('VACANCY','Вакансии'),('WORKING','В работе'),('UNKNOWN','Уточнить статус')])
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        if not self['state'].value():self.initial['state']=''
        self.fields['construction_object'].queryset=ConstructionObject.objects.filter(company=company)
        self.fields['camps'].queryset=Camp.objects.filter(company=company)
        self.fields['position'].queryset=Position.objects.filter(company=company).order_by('name')
        self.status_explanations=[(label,STATUS_HELP[value]) for value,label in self.fields['state'].choices]
        self.company=company
    def clean(self):
        data=super().clean();obj=data.get('construction_object');camps=data.get('camps')
        if obj and camps:
            ids={obj.pk};pairs=list(ConstructionObject.objects.filter(company=self.company,project=obj.project).values_list('pk','parent_id'))
            while True:
                children={pk for pk,parent in pairs if parent in ids}
                if children<=ids:break
                ids.update(children)
            invalid=camps.exclude(object_links__company=self.company,object_links__construction_object_id__in=ids).distinct()
            if invalid.exists():self.add_error('camps','Выберите только ВЖГ, относящиеся к выбранному проекту.')
        return data
    def selected_camps(self,company):
        return self.cleaned_data['camps'].order_by('name','pk')


def normalize_rotation_filter_data(data,company):
    """Always choose one project; default its WJG filter to every linked camp."""
    from .models import CampObjectLink
    values=data.copy();camp_ids=values.getlist('camps') if hasattr(values,'getlist') else values.get('camps') or []
    if not isinstance(camp_ids,(list,tuple)):camp_ids=[camp_ids]
    camp_ids=[str(value) for value in camp_ids if str(value)]
    direct_camp=values.get('camp')
    if direct_camp:camp_ids=[str(direct_camp)]
    object_id=values.get('construction_object')
    if camp_ids and not object_id:
        link=CampObjectLink.objects.filter(company=company,camp_id=camp_ids[0]).order_by('construction_object__name','pk').first()
        if link:object_id=link.construction_object_id
    elif object_id:
        try:obj=ConstructionObject.objects.get(company=company,pk=object_id)
        except (ConstructionObject.DoesNotExist,ValueError,TypeError):obj=None
        if obj:
            ids={obj.pk};pairs=list(ConstructionObject.objects.filter(company=company,project=obj.project).values_list('pk','parent_id'))
            while True:
                children={pk for pk,parent in pairs if parent in ids}
                if children<=ids:break
                ids.update(children)
            allowed=list(CampObjectLink.objects.filter(company=company,construction_object_id__in=ids,camp__is_active=True).order_by('camp__name','pk').values_list('camp_id',flat=True).distinct())
            # A project change can submit the WJG selection left by the previous
            # project. Treat that as a dependent-filter change and select every
            # WJG of the new project; genuine subsets remain untouched.
            if not camp_ids or not set(camp_ids)<=set(map(str,allowed)):camp_ids=allowed
    elif not camp_ids and not object_id:
        link=CampObjectLink.objects.filter(company=company,camp__is_active=True).order_by('construction_object__name','camp__name','pk').first()
        if link:
            object_id=link.construction_object_id
            obj=link.construction_object;ids={obj.pk};pairs=list(ConstructionObject.objects.filter(company=company,project=obj.project).values_list('pk','parent_id'))
            while True:
                children={pk for pk,parent in pairs if parent in ids}
                if children<=ids:break
                ids.update(children)
            camp_ids=list(CampObjectLink.objects.filter(company=company,construction_object_id__in=ids,camp__is_active=True).order_by('camp__name','pk').values_list('camp_id',flat=True).distinct())
    if object_id:values['construction_object']=str(object_id)
    if camp_ids:
        if hasattr(values,'setlist'):values.setlist('camps',[str(value) for value in camp_ids])
        else:values['camps']=[str(value) for value in camp_ids]
    return values


def rotation_project_camps(company):
    """Return WJG ids available for every selectable project/object node."""
    from .models import CampObjectLink
    objects=list(ConstructionObject.objects.filter(company=company).values_list('pk','project_id','parent_id'))
    linked=defaultdict(set)
    for object_id,camp_id in CampObjectLink.objects.filter(company=company,camp__is_active=True).values_list('construction_object_id','camp_id'):
        linked[object_id].add(camp_id)
    parents={pk:parent_id for pk,_,parent_id in objects}
    # Accumulate each direct link into all its ancestors once. This is linear
    # for ordinary project trees instead of walking every subtree separately.
    result=defaultdict(set)
    for object_id,camp_ids in linked.items():
        current=object_id;seen=set()
        while current and current not in seen:
            seen.add(current);result[current].update(camp_ids);current=parents.get(current)
    return {str(pk):sorted(result[pk]) for pk,_,_ in objects}


def _day_code(row):
    value=row.get('today_code')
    return str(value).strip().upper() if value is not None else ''


def _trip_direction(calendar,day):
    """Only classify recorded travel days when the next known day gives direction."""
    current=day
    while str(calendar.get(str(current),'')).strip().upper()=='ДП':current+=timedelta(days=1)
    if current==day:return ''
    next_code=str(calendar.get(str(current),'')).strip().upper()
    return 'ARRIVING' if is_work(next_code) else 'DEPARTING' if next_code=='МО' else ''


def _planned_return(calendar,day,after=None):
    work=sorted(key for key,value in calendar.items() if is_work(value) and (after is None or key>=str(after)))
    if after is not None:return work[0] if work else None
    if is_work(calendar.get(str(day))):
        starts=[key for key in work if key<=str(day) and not is_work(calendar.get(str(date.fromisoformat(key)-timedelta(days=1))))]
        if starts:return starts[-1]
    return next((key for key in work if key>=str(day)),None)


def rotation_stats(rows):
    """Count roster people, never work hours or empty slots as workers."""
    people=[row for row in rows if row.get('person')]
    return {'people':len(people),'present':sum(bool(row.get('verified')) for row in people),
        'on_shift':sum(row['state']=='MV' for row in people),'rest':sum(row['state']=='MO' for row in people),
        'working':sum(is_work(_day_code(row)) for row in people),
        'travelling':sum(_day_code(row)=='ДП' for row in people),
        'no_schedule':sum(not _day_code(row) for row in people),
        'vacancies':sum(not row.get('person') for row in rows),'unknown':sum(row['state']=='UNKNOWN' for row in people)}


def rotation_rows(company,camps,day,*,query='',position=None,state='',include_unassigned=False):
    from .rotation_calendar import stays_by_person,anchor_for,description,cycle_definition,effective_schedule,schedule_label
    schedule_stays=stays_by_person(company)
    camp_ids={camp.pk for camp in camps};camp_names={camp.name.strip().casefold():camp for camp in camps}
    from .plan_assignments import active_on
    slots=list(StaffingSlot.objects.filter(company=company).filter(Q(person=None)|Q(person__dispatcher_removed_at=None)).filter(active_on(day)).select_related('group__board','housing_camp','planning_position__group','construction_object','person__employee__position__planning_mapping__brigade__group','person__schedule_slot').prefetch_related('pattern','person__schedule_slot__pattern'))
    removed_people=set(StaffingSlot.objects.filter(company=company,planning_position__isnull=False,cancelled_at__isnull=False,person__isnull=False).values_list('person_id',flat=True))
    all_assignments=defaultdict(list)
    for slot in slots:
        if slot.person_id:all_assignments[slot.person_id].append(slot)
    def slot_camp(slot):return slot.housing_camp or camp_names.get(slot.camp.strip().casefold())
    def belongs(slot):return (slot_camp(slot) and slot_camp(slot).pk in camp_ids) or include_unassigned and not slot.housing_camp_id and not slot.camp
    relevant=[slot for slot in slots if belongs(slot)];boards={slot.group.board_id for slot in relevant}
    vacant=[slot for slot in slots if not slot.person_id and (belongs(slot) or slot.group.board_id in boards and not slot.housing_camp_id and not slot.camp)]
    by_person=defaultdict(list)
    for slot in relevant:
        if slot.person_id:by_person[slot.person_id].append(slot)
    all_stays=list(CampStay.objects.filter(company=company,person__dispatcher_removed_at=None,cancelled_at=None,arrived_on__lte=day).select_related('camp','person__employee__position__planning_mapping__brigade__group','person__schedule_slot__group__board').prefetch_related('person__schedule_slot__pattern').order_by('-arrived_on','-pk'))
    future_stays={}
    for stay in CampStay.objects.filter(company=company,person__dispatcher_removed_at=None,cancelled_at=None,arrived_on__gt=day,
            camp_id__in=camp_ids).select_related('camp','person__employee__position__planning_mapping__brigade__group','person__schedule_slot__group__board').order_by('arrived_on','pk'):
        future_stays.setdefault(stay.person_id,stay)
    current={};last={};departures={}
    for stay in all_stays:
        last.setdefault(stay.person_id,stay)
        if stay.camp_id in camp_ids and stay.departed_on==day:departures.setdefault(stay.person_id,stay)
        if (stay.departed_on is None or stay.departed_on>day) and stay.arrived_on!=stay.departed_on:current.setdefault(stay.person_id,stay)
    from .models import HousingEvent
    initial={row.person_id:row for row in HousingEvent.objects.filter(company=company,person__dispatcher_removed_at=None,kind='DEPARTURE',stay=None,date__lte=day).select_related('person__employee__position__planning_mapping__brigade__group','from_camp').order_by('date','pk')}
    people={slot.person_id:slot.person for slot in relevant if slot.person_id}
    for person_id,event in initial.items():
        if event.from_camp_id in camp_ids:people[person_id]=event.person
    for person_id,stay in {**last,**current}.items():
        if stay.camp_id in camp_ids:people[person_id]=stay.person
    for person_id,stay in future_stays.items():people.setdefault(person_id,stay.person)
    if state=='DEPARTING':
        for person_id,stay in departures.items():people[person_id]=stay.person
    from .models import ScheduleChangeRequest
    pending_schedules={row.person_id:row for row in ScheduleChangeRequest.objects.filter(company=company,person_id__in=people,status='PENDING').select_related('requested_schedule')}
    from .models import MovementReviewRequest
    active_routes={}
    for route in MovementReviewRequest.objects.filter(company=company,person_id__in=people,status__in=['PENDING_APPROVAL','RETURNED','PEREVAKHTA','AT_HUB','EN_ROUTE']).order_by('created_at','pk'):
        active_routes[route.person_id]=route
    days=[day.replace(day=n) for n in range(1,monthrange(day.year,day.month)[1]+1)];rows=[]
    for person_id,person in people.items():
        active=current.get(person_id);previous=last.get(person_id);future=future_stays.get(person_id);initial_exit=initial.get(person_id);assigned=by_person[person_id]
        elsewhere=None
        if active and active.camp_id not in camp_ids and not (state=='DEPARTING' and person_id in departures):
            if any(slot.planning_position_id for slot in assigned):
                elsewhere=active;active=None;previous=None;initial_exit=None
            else:continue
        if not active and previous and previous.camp_id not in camp_ids and not assigned:continue
        assignments=all_assignments.get(person_id,[])
        planned=[slot for slot in assignments if slot.planning_position_id]
        slot=max(planned,key=lambda slot:(slot.assigned_from or date.min,slot.pk)) if planned else housing.schedule_slot(person,assignments)
        display_slot=slot or (assigned[0] if assigned else None)
        status='MV' if active else 'MO' if previous and previous.departed_on and previous.departed_on<=day else 'MO' if initial_exit else 'MV' if any(s.status=='МВ' for s in assigned) else 'MO' if any(s.status=='МО' for s in assigned) else 'UNKNOWN'
        camp=active.camp if active else future.camp if future else initial_exit.from_camp if initial_exit and not previous else slot_camp(display_slot) if display_slot else previous.camp if previous else None
        personal=housing.schedule_slot(person,assignments)
        graph_slot=personal if personal and personal.pattern_id else slot
        schedule=effective_schedule(graph_slot,person)
        calendar=calendar_for(schedule,start=day.replace(day=1),end=day+timedelta(days=730),stays=schedule_stays.get(person_id,[]));start=active.arrived_on if active else slot.arrival_date if slot and slot.arrival_date else day
        origin=anchor_for(schedule_stays.get(person_id,[]),day)
        if active and origin and origin[1]=='ARRIVAL':start=origin[0]
        known_cycle=cycle_definition(schedule.pattern) if schedule and schedule.pattern_id else None
        outgoing=next(iter(departure_days(calendar,start)),'') if status=='MV' else ''
        if active and origin and origin[1]=='ARRIVAL' and known_cycle:
            due=str(origin[0]+timedelta(days=known_cycle['on_days']))
            outgoing=min(outgoing,due) if outgoing else due
        departure=date.fromisoformat(outgoing) if outgoing else None;delta=(departure-day).days if departure else None
        rest_started=previous.departed_on if previous and previous.departed_on and previous.departed_on<=day else slot.departure_date if slot and slot.departure_date and slot.departure_date<=day else None
        upcoming=_planned_return(calendar,day,rest_started) if status in {'MO','UNKNOWN'} else None
        if not active and origin and origin[1]=='DEPARTURE' and known_cycle:
            upcoming=str(origin[0]+timedelta(days=known_cycle['off_days']))
        state_since=None;state_source=''
        if status=='MV':
            state_since=origin[0] if active and origin and origin[1]=='ARRIVAL' else active.arrived_on if active else slot.arrival_date if slot else None
            state_source='Факт' if active else 'Начальные данные'
        elif status=='MO':
            state_since=origin[0] if origin and origin[1]=='DEPARTURE' else initial_exit.date if initial_exit else rest_started
            state_source='Факт' if origin and origin[1]=='DEPARTURE' else 'Начальные данные'
        state_days=(day-state_since).days+1 if state_since and state_since<=day else None
        trip=_trip_direction(calendar,day)
        arriving=trip=='ARRIVING' or bool(active and active.camp_id in camp_ids and active.arrived_on==day) or bool(not active and slot and slot.arrival_date==day) or upcoming==str(day)
        departing=trip=='DEPARTING' or outgoing==str(day) or person_id in departures or bool(slot and slot.departure_date==day)
        employee=person.employee if person.employee_id else None
        assignment_removed=not planned and person_id in removed_people
        role=slot.planning_position.name if slot and slot.planning_position_id else 'Без назначения' if assignment_removed else str(employee.position) if employee and employee.position_id else display_slot.group.position if display_slot else 'Должность не указана'
        rows.append({'person':person,'slot':slot,'schedule_slot':graph_slot,'stay':active,'camp':camp,'position':employee.position if employee and employee.position_id else None,'position_label':role,
            'planning_position_id':slot.planning_position_id if slot else None,'assignment_object':slot.construction_object if slot and slot.construction_object_id else None,
            'assignment_removed':assignment_removed,
            'actual_elsewhere':elsewhere.camp if elsewhere else None,
            'staff_position_label':str(employee.position) if employee and employee.position_id and (assignment_removed or slot and slot.planning_position_id and str(employee.position)!=role) else '',
            'state':status,'state_label':{'MV':'На вахте','MO':'Межвахтовый отдых','UNKNOWN':'Требует уточнения'}[status], 'verified':bool(active),'legacy_only':status=='MV' and not active,
            'movement_route_status':active_routes[person_id].status if person_id in active_routes else '',
            'movement_route_kind':active_routes[person_id].kind if person_id in active_routes else '',
            'actual_rest_started':bool(not active and previous and previous.departed_on and previous.departed_on<=day),
            'arriving':arriving,'future_arrival':bool(future),'planned_arrival':future.arrived_on if future else None,
            'departing':departing,'departure_camp':departures[person_id].camp if person_id in departures else None,
            'schedule_info':description(schedule,day,schedule_stays.get(person_id,[])),
            'cycle_missing':not known_cycle,
            'schedule_cycle_label':f"{known_cycle['on_days']}/{known_cycle['off_days']}" if known_cycle else '',
            'schedule_from_zup':getattr(schedule,'schedule_from_zup',False),
            'schedule_change_pending':pending_schedules.get(person_id),
            'state_since':state_since,'state_days':state_days,'state_days_source':state_source,
            'schedule_label':schedule_label(schedule,person),
            'arrival_overdue':bool(not active and status!='MV' and upcoming and upcoming<str(day)),
            'arrived_on':active.arrived_on if active else previous.arrived_on if previous else slot.arrival_date if slot else None,'arrival_is_actual':bool(active or previous),'last_departure':previous.departed_on if previous else initial_exit.date if initial_exit else None,
            'planned_departure_display':departure,'planned_departure':outgoing,'departure_days':delta,
            'departure_warning':('overdue' if delta<0 else 'soon' if delta<=14 else '') if delta is not None else '',
            'planned_return':date.fromisoformat(upcoming) if upcoming else None,'codes':[calendar.get(str(d),'') for d in days],'today_code':calendar.get(str(day),'')})
    for slot in vacant:
        calendar=calendar_for(slot)
        rows.append({'person':None,'slot':slot,'camp':slot_camp(slot),'position_label':slot.group.position,'state':'VACANCY','state_label':'Открытая вакансия','schedule_label':str(slot.pattern) if slot.pattern_id else 'Не назначен','codes':[calendar.get(str(d),'') for d in days],'today_code':calendar.get(str(day),'')})
    if query:
        terms=query.casefold().split()
        rows=[row for row in rows if all(term in (' '.join([row['person'].display_name if row['person'] else 'Вакансия',row['person'].display_employee_number if row['person'] else '',row['position_label']])).casefold() for term in terms)]
    if position:rows=filter_position(rows,position)
    if state=='WORKING':rows=[row for row in rows if row.get('person') and is_work(_day_code(row))]
    elif state=='TRAVEL':rows=[row for row in rows if row.get('person') and _day_code(row)=='ДП']
    elif state=='ARRIVING':rows=[row for row in rows if row.get('person') and row.get('arriving')]
    elif state=='DEPARTING':rows=[row for row in rows if row.get('person') and row.get('departing')]
    elif state=='DEPARTURE_OVERDUE':rows=[row for row in rows if row.get('person') and row.get('departure_warning')=='overdue']
    elif state=='DEPARTURE_PENDING':rows=[row for row in rows if row.get('person') and row.get('departure_warning')=='soon']
    elif state=='ARRIVAL_OVERDUE':rows=[row for row in rows if row.get('person') and row.get('arrival_overdue')]
    elif state:rows=[row for row in rows if row['state']==state]
    from apps.resources.group_order import position_order
    from apps.resources.models import Position
    positions=list(Position.objects.filter(company=company).select_related('planning_mapping__brigade__group'))
    vacant_positions={p.name.casefold():p for p in positions}
    plan_positions={p.planning_mapping.brigade_id:p for p in positions if getattr(p,'planning_mapping',None)}
    for row in rows:row['sort_position']=plan_positions.get(row.get('planning_position_id')) or row.get('position') or vacant_positions.get(row['position_label'].casefold())
    return sorted(rows,key=lambda row:(position_order(row.get('sort_position'),row['position_label']),row['state']=='VACANCY',row['person'].display_name.casefold() if row['person'] else '',row['slot'].pk if row['slot'] else 0)),days,rotation_stats(rows)


def positions_in_rows(company,rows):
    ids={row['position'].pk for row in rows if row.get('position')}
    labels={row.get('position_label','').strip().casefold() for row in rows}
    ids.update(pk for pk,name in Position.objects.filter(company=company).values_list('pk','name') if name.strip().casefold() in labels)
    ids.update(Position.objects.filter(company=company,planning_mapping__brigade_id__in={row['planning_position_id'] for row in rows if row.get('planning_position_id')}).values_list('pk',flat=True))
    from apps.resources.group_order import ordered
    return ordered(Position.objects.filter(company=company,pk__in=ids),group_path='planning_mapping__brigade__group')


def filter_position(rows,position):
    if position is None:return rows
    mapping=getattr(position,'planning_mapping',None)
    return [row for row in rows if row.get('position') and row['position'].pk==position.pk or row.get('position_label','').strip().casefold()==position.name.strip().casefold() or mapping and row.get('planning_position_id')==mapping.brigade_id]


def status_indicator(row):
    if row.get('movement_route_status')=='AT_HUB':return 'blue','В ОП Дудинка','Сотрудник прибыл в ОП Дудинка. Билеты и обходной лист оформляются параллельно.'
    if row.get('movement_route_status')=='EN_ROUTE':return 'blue','В пути в ВЖГ','Сотрудник выехал из ОП Дудинка и ещё не прибыл в ВЖГ.'
    if row.get('future_arrival'):return 'blue',f"Ожидается заезд {row['planned_arrival'].strftime('%d.%m.%Y')}",'Заезд подтверждён будущей датой. До этой даты сотрудник не входит в фактическую численность ВЖГ.'
    if row.get('departure_warning')=='overdue':return 'red','Выезд просрочен',STATUS_HELP['DEPARTURE_OVERDUE']
    if row.get('arrival_overdue'):return 'red','Заезд просрочен',STATUS_HELP['ARRIVAL_OVERDUE']
    if row.get('departure_warning')=='soon':return 'yellow','Ожидает выезда',STATUS_HELP['DEPARTURE_PENDING']
    if row.get('legacy_only'):return 'yellow','Заезд не оформлен','На вахте по начальным данным. Оформите фактический заезд, проверив дату и ВЖГ.'
    if row.get('state')=='MO' and row.get('actual_rest_started'):return 'muted','Межвахтовый отдых',STATUS_HELP['MO']
    if row.get('today_code')=='ДП':return 'blue','В пути',STATUS_HELP['TRAVEL']
    if row.get('state')=='MV':return 'green','На вахте',STATUS_HELP['MV']
    if row.get('state')=='MO':return 'muted','Межвахтовый отдых',STATUS_HELP['MO']
    if row.get('state')=='VACANCY':return 'muted','Вакансия',STATUS_HELP['VACANCY']
    return 'yellow','Уточнить статус',STATUS_HELP['UNKNOWN']


def employee_schedule_label(employee):
    return ((employee.zup_card or {}).get('schedule') or '').strip() or 'Не указан' if employee else 'Не указан'
