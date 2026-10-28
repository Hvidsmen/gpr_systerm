"""Rotation includes people at work, people on rest, and vacant staffing slots.
Actual residence remains a separate source of production eligibility.
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


class RotationFilter(forms.Form):
    construction_object=forms.ModelChoiceField(label='Объект и его субобъекты',queryset=ConstructionObject.objects.none(),required=False,empty_label='Все объекты')
    camps=forms.ModelMultipleChoiceField(label='ВЖГ · можно выбрать несколько',queryset=Camp.objects.none(),required=False)
    date=forms.DateField(label='На дату',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    q=forms.CharField(label='Сотрудник',required=False,widget=forms.TextInput(attrs={'placeholder':'ФИО, должность или табельный номер'}))
    position=forms.ModelChoiceField(label='Должность',queryset=Position.objects.none(),required=False,empty_label='Все должности')
    state=forms.ChoiceField(label='Вахта / отдых / вакансии',required=False,choices=[('','Все'),('MV','На вахте'),('MO','На межвахтовом отдыхе'),('VACANCY','Открытые вакансии'),('UNKNOWN','Требует уточнения')])
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['construction_object'].queryset=ConstructionObject.objects.filter(company=company)
        self.fields['camps'].queryset=Camp.objects.filter(company=company)
        self.fields['position'].queryset=Position.objects.filter(company=company).order_by('name')
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


def rotation_rows(company,camps,day,*,query='',position=None,state='',include_unassigned=False):
    camp_ids={camp.pk for camp in camps};camp_names={camp.name.strip().casefold():camp for camp in camps}
    slots=list(StaffingSlot.objects.filter(company=company).select_related('group__board','housing_camp','pattern','person__employee__position','person__schedule_slot__pattern'))
    def slot_camp(slot):return slot.housing_camp or camp_names.get(slot.camp.strip().casefold())
    def belongs(slot):return (slot_camp(slot) and slot_camp(slot).pk in camp_ids) or include_unassigned and not slot.housing_camp_id and not slot.camp
    relevant=[slot for slot in slots if belongs(slot)];boards={slot.group.board_id for slot in relevant}
    vacant=[slot for slot in slots if not slot.person_id and (belongs(slot) or slot.group.board_id in boards and not slot.housing_camp_id and not slot.camp)]
    by_person=defaultdict(list)
    for slot in relevant:
        if slot.person_id:by_person[slot.person_id].append(slot)
    all_stays=list(CampStay.objects.filter(company=company,cancelled_at=None,arrived_on__lte=day).select_related('camp','person__employee__position','person__schedule_slot__pattern','person__schedule_slot__group__board').order_by('-arrived_on','-pk'))
    current={};last={}
    for stay in all_stays:
        last.setdefault(stay.person_id,stay)
        if (stay.departed_on is None or stay.departed_on>day) and stay.arrived_on!=stay.departed_on:current.setdefault(stay.person_id,stay)
    people={slot.person_id:slot.person for slot in relevant if slot.person_id}
    for person_id,stay in {**last,**current}.items():
        if stay.camp_id in camp_ids:people[person_id]=stay.person
    days=[day.replace(day=n) for n in range(1,monthrange(day.year,day.month)[1]+1)];rows=[]
    for person_id,person in people.items():
        active=current.get(person_id);previous=last.get(person_id);assigned=by_person[person_id]
        if active and active.camp_id not in camp_ids:continue
        if not active and previous and previous.camp_id not in camp_ids and not assigned:continue
        slot=next((slot for slot in assigned if slot.pk==person.schedule_slot_id),None) or (assigned[0] if assigned else housing.schedule_slot(person))
        status='MV' if active else 'MO' if previous and previous.departed_on and previous.departed_on<=day else 'MV' if any(s.status=='МВ' for s in assigned) else 'MO' if any(s.status=='МО' for s in assigned) else 'UNKNOWN'
        camp=active.camp if active else slot_camp(slot) if slot else previous.camp if previous else None
        calendar=calendar_for(slot) if slot else {};start=active.arrived_on if active else slot.arrival_date if slot and slot.arrival_date else day
        outgoing=next(iter(departure_days(calendar,start)),'') if status=='MV' else ''
        departure=date.fromisoformat(outgoing) if outgoing else None;delta=(departure-day).days if departure else None
        upcoming=next((key for key,value in sorted(calendar.items()) if key>=str(day) and is_work(value)),None) if status=='MO' else None
        employee=person.employee if person.employee_id else None
        role=str(employee.position) if employee and employee.position_id else slot.group.position if slot else 'Должность не указана'
        rows.append({'person':person,'slot':slot,'stay':active,'camp':camp,'position':employee.position if employee and employee.position_id else None,'position_label':role,
            'state':status,'state_label':{'MV':'На вахте','MO':'Межвахтовый отдых','UNKNOWN':'Требует уточнения'}[status], 'verified':bool(active),'legacy_only':status=='MV' and not active,
            'arrived_on':active.arrived_on if active else slot.arrival_date if slot else None,'last_departure':previous.departed_on if previous else None,
            'planned_departure_display':departure,'planned_departure':outgoing,'departure_days':delta,
            'departure_warning':('overdue' if delta<0 else 'soon' if delta<=14 else '') if delta is not None else '',
            'planned_return':date.fromisoformat(upcoming) if upcoming else None,'codes':[calendar.get(str(d),'') for d in days],'today_code':calendar.get(str(day),'')})
    for slot in vacant:
        calendar=calendar_for(slot)
        rows.append({'person':None,'slot':slot,'camp':slot_camp(slot),'position_label':slot.group.position,'state':'VACANCY','state_label':'Открытая вакансия','codes':[calendar.get(str(d),'') for d in days],'today_code':calendar.get(str(day),'')})
    stats={'people':sum(bool(row['person']) for row in rows),'present':sum(bool(row.get('verified')) for row in rows),
        'on_shift':sum(row['state']=='MV' for row in rows),'rest':sum(row['state']=='MO' for row in rows),'vacancies':len(vacant),'unknown':sum(row['state']=='UNKNOWN' for row in rows)}
    if query:
        terms=query.casefold().split()
        rows=[row for row in rows if all(term in (' '.join([row['person'].display_name if row['person'] else 'Вакансия',row['person'].display_employee_number if row['person'] else '',row['position_label']])).casefold() for term in terms)]
    if position:rows=[row for row in rows if row.get('position') and row['position'].pk==position.pk or not row.get('person') and row['position_label'].casefold()==position.name.casefold()]
    if state:rows=[row for row in rows if row['state']==state]
    return sorted(rows,key=lambda row:(row['position_label'].casefold(),row['state']=='VACANCY',row['person'].display_name.casefold() if row['person'] else '',row['slot'].pk if row['slot'] else 0)),days,stats
