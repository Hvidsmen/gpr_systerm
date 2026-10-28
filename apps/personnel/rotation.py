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


class RotationStatusSelect(forms.RadioSelect):
    def create_option(self,name,value,label,selected,index,subindex=None,attrs=None):
        option=super().create_option(name,value,label,selected,index,subindex,attrs)
        option['attrs']['title']=STATUS_HELP.get(str(value),'')
        option['attrs']['aria-description']=option['attrs']['title']
        return option


class RotationFilter(forms.Form):
    construction_object=forms.ModelChoiceField(label='Объект',queryset=ConstructionObject.objects.none(),required=False,empty_label='Все объекты')
    camps=forms.ModelMultipleChoiceField(label='ВЖГ',queryset=Camp.objects.none(),required=False)
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
    def selected_camps(self,company):
        camps=self.fields['camps'].queryset
        obj=self.cleaned_data.get('construction_object')
        if obj:
            ids={obj.pk};pairs=list(ConstructionObject.objects.filter(company=company,project=obj.project).values_list('pk','parent_id'))
            while True:
                children={pk for pk,parent in pairs if parent in ids}
                if children<=ids:break
                ids.update(children)
            camps=camps.filter(object_links__company=company,object_links__construction_object_id__in=ids).distinct()
        if self.cleaned_data.get('camps'):camps=camps.filter(pk__in=self.cleaned_data['camps'])
        return camps.order_by('name','pk')


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
    from .rotation_calendar import stays_by_person,anchor_for,description,cycle_definition
    schedule_stays=stays_by_person(company)
    camp_ids={camp.pk for camp in camps};camp_names={camp.name.strip().casefold():camp for camp in camps}
    slots=list(StaffingSlot.objects.filter(company=company).select_related('group__board','housing_camp','pattern','person__employee__position','person__schedule_slot__pattern'))
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
    all_stays=list(CampStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day).select_related('camp','person__employee__position','person__schedule_slot__pattern','person__schedule_slot__group__board').order_by('-arrived_on','-pk'))
    current={};last={};departures={}
    for stay in all_stays:
        last.setdefault(stay.person_id,stay)
        if stay.camp_id in camp_ids and stay.departed_on==day:departures.setdefault(stay.person_id,stay)
        if (stay.departed_on is None or stay.departed_on>day) and stay.arrived_on!=stay.departed_on:current.setdefault(stay.person_id,stay)
    people={slot.person_id:slot.person for slot in relevant if slot.person_id}
    for person_id,stay in {**last,**current}.items():
        if stay.camp_id in camp_ids:people[person_id]=stay.person
    if state=='DEPARTING':
        for person_id,stay in departures.items():people[person_id]=stay.person
    days=[day.replace(day=n) for n in range(1,monthrange(day.year,day.month)[1]+1)];rows=[]
    for person_id,person in people.items():
        active=current.get(person_id);previous=last.get(person_id);assigned=by_person[person_id]
        if active and active.camp_id not in camp_ids and not (state=='DEPARTING' and person_id in departures):continue
        if not active and previous and previous.camp_id not in camp_ids and not assigned:continue
        slot=housing.schedule_slot(person,all_assignments.get(person_id,[]))
        display_slot=slot or (assigned[0] if assigned else None)
        status='MV' if active else 'MO' if previous and previous.departed_on and previous.departed_on<=day else 'MV' if any(s.status=='МВ' for s in assigned) else 'MO' if any(s.status=='МО' for s in assigned) else 'UNKNOWN'
        camp=active.camp if active else slot_camp(display_slot) if display_slot else previous.camp if previous else None
        calendar=calendar_for(slot,start=day.replace(day=1),end=day+timedelta(days=730),stays=schedule_stays.get(person_id,[])) if slot else {};start=active.arrived_on if active else slot.arrival_date if slot and slot.arrival_date else day
        origin=anchor_for(schedule_stays.get(person_id,[]),day)
        if active and origin and origin[1]=='ARRIVAL':start=origin[0]
        known_cycle=cycle_definition(slot.pattern) if slot and slot.pattern_id else None
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
            state_since=origin[0] if origin and origin[1]=='DEPARTURE' else rest_started
            state_source='Факт' if origin and origin[1]=='DEPARTURE' else 'Начальные данные'
        state_days=(day-state_since).days+1 if state_since and state_since<=day else None
        trip=_trip_direction(calendar,day)
        arriving=trip=='ARRIVING' or bool(active and active.camp_id in camp_ids and active.arrived_on==day) or bool(not active and slot and slot.arrival_date==day) or upcoming==str(day)
        departing=trip=='DEPARTING' or outgoing==str(day) or person_id in departures or bool(slot and slot.departure_date==day)
        employee=person.employee if person.employee_id else None
        role=str(employee.position) if employee and employee.position_id else display_slot.group.position if display_slot else 'Должность не указана'
        rows.append({'person':person,'slot':slot,'stay':active,'camp':camp,'position':employee.position if employee and employee.position_id else None,'position_label':role,
            'state':status,'state_label':{'MV':'На вахте','MO':'Межвахтовый отдых','UNKNOWN':'Требует уточнения'}[status], 'verified':bool(active),'legacy_only':status=='MV' and not active,
            'arriving':arriving,'departing':departing,'departure_camp':departures[person_id].camp if person_id in departures else None,
            'schedule_info':description(slot,day,schedule_stays.get(person_id,[])),
            'cycle_missing':not known_cycle,
            'state_since':state_since,'state_days':state_days,'state_days_source':state_source,
            'schedule_label':str(slot.pattern) if slot and slot.pattern_id else 'Не назначен',
            'arrival_overdue':bool(not active and status!='MV' and upcoming and upcoming<str(day)),
            'arrived_on':active.arrived_on if active else slot.arrival_date if slot else None,'last_departure':previous.departed_on if previous else None,
            'planned_departure_display':departure,'planned_departure':outgoing,'departure_days':delta,
            'departure_warning':('overdue' if delta<0 else 'soon' if delta<=14 else '') if delta is not None else '',
            'planned_return':date.fromisoformat(upcoming) if upcoming else None,'codes':[calendar.get(str(d),'') for d in days],'today_code':calendar.get(str(day),'')})
    for slot in vacant:
        calendar=calendar_for(slot)
        rows.append({'person':None,'slot':slot,'camp':slot_camp(slot),'position_label':slot.group.position,'state':'VACANCY','state_label':'Открытая вакансия','schedule_label':str(slot.pattern) if slot.pattern_id else 'Не назначен','codes':[calendar.get(str(d),'') for d in days],'today_code':calendar.get(str(day),'')})
    if query:
        terms=query.casefold().split()
        rows=[row for row in rows if all(term in (' '.join([row['person'].display_name if row['person'] else 'Вакансия',row['person'].display_employee_number if row['person'] else '',row['position_label']])).casefold() for term in terms)]
    if position:rows=[row for row in rows if row.get('position') and row['position'].pk==position.pk or not row.get('person') and row['position_label'].casefold()==position.name.casefold()]
    if state=='WORKING':rows=[row for row in rows if row.get('person') and is_work(_day_code(row))]
    elif state=='TRAVEL':rows=[row for row in rows if row.get('person') and _day_code(row)=='ДП']
    elif state=='ARRIVING':rows=[row for row in rows if row.get('person') and row.get('arriving')]
    elif state=='DEPARTING':rows=[row for row in rows if row.get('person') and row.get('departing')]
    elif state=='DEPARTURE_OVERDUE':rows=[row for row in rows if row.get('person') and row.get('departure_warning')=='overdue']
    elif state=='DEPARTURE_PENDING':rows=[row for row in rows if row.get('person') and row.get('departure_warning')=='soon']
    elif state=='ARRIVAL_OVERDUE':rows=[row for row in rows if row.get('person') and row.get('arrival_overdue')]
    elif state:rows=[row for row in rows if row['state']==state]
    return sorted(rows,key=lambda row:(row['position_label'].casefold(),row['state']=='VACANCY',row['person'].display_name.casefold() if row['person'] else '',row['slot'].pk if row['slot'] else 0)),days,rotation_stats(rows)
