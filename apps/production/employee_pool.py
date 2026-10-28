"""All active employees; dated camp presence affects presentation, not eligibility."""
from django.db.models import Q
from apps.resources.models import Employee
from apps.personnel.models import CampStay,StaffingSlot
from apps.personnel.housing import present_stays

GENERAL_PATH='employee__position__planning_mapping__brigade__group__is_general_staff'


def activity_direction(row):
    if row.entry_direction:return row.entry_direction
    code=row.reported_by.role.code if row.reported_by_id and row.reported_by.role_id else None
    if code=='DISPATCHER':return 'works'
    if code=='PERSONNEL_DISPATCHER':return 'labor'
    from .production_entry import is_general_employee
    return 'labor' if is_general_employee(row.employee) else 'works'


def labor_direction():
    return Q(entry_direction='labor')|(Q(entry_direction='')&(Q(reported_by__role__code='PERSONNEL_DISPATCHER')|(Q(**{GENERAL_PATH:True})&~Q(reported_by__role__code='DISPATCHER'))))


def prepare(obj,day):
    employees=list(Employee.objects.filter(company=obj.company,is_active=True).select_related('position').order_by('last_name','first_name','middle_name','employee_number','pk'))
    stays={s.person.employee_id:s for s in present_stays(obj.company,day).filter(person__employee__company=obj.company).exclude(person__employee=None).select_related('camp','person__employee')}
    linked=set(obj.camp_links.filter(company=obj.company).values_list('camp_id',flat=True))
    # Closed actual stays take precedence over an undated legacy table status.
    last={}
    for stay in CampStay.objects.filter(company=obj.company,cancelled_at=None,arrived_on__lte=day,person__employee__isnull=False).select_related('person').order_by('-arrived_on','-pk'):
        last.setdefault(stay.person.employee_id,stay)
    legacy={}
    for slot in StaffingSlot.objects.filter(company=obj.company,person__employee__isnull=False).values('person__employee_id','status'):
        legacy.setdefault(slot['person__employee_id'],set()).add(slot['status'])
    employees.sort(key=lambda e:(e.display_name.casefold(),e.employee_number.casefold(),e.pk))
    locations={};groups=[[],[],[]]
    for employee in employees:
        stay=stays.get(employee.pk);previous=last.get(employee.pk)
        group=0 if stay and stay.camp_id in linked else 1 if stay else 2
        rest=bool(not stay and (previous and previous.departed_on and previous.departed_on<=day or not previous and legacy.get(employee.pk)=={'МО'}))
        text=('ВЖГ: '+stay.camp.name) if stay else 'Нахождение в ВЖГ не оформлено'+(' · межвахтовый отдых' if rest else '')
        locations[str(employee.pk)]={'text':text,'camp':stay.camp.name if stay else '', 'camp_id':stay.camp_id if stay else None,'group':group,'rest':rest,'position':employee.position_id,'name':employee.display_name,'number':employee.employee_number,'position_label':employee.display_position}
        label=' · '.join(filter(None,[employee.display_name,'№ '+employee.employee_number if employee.employee_number else '',employee.display_position or 'Должность не указана',text]))
        groups[group].append((employee.pk,label))
    return {'locations':locations,'groups':[(label,values) for label,values in zip(['ВЖГ, связанные с объектом','Другие ВЖГ','Без оформленного нахождения в ВЖГ'],groups) if values]}


def apply(field,pool,retained=None):
    choices=[('',field.empty_label or 'Выберите сотрудника'),*pool['groups']]
    if retained and not retained.is_active:
        label=retained.display_name+(' · № '+retained.employee_number if retained.employee_number else '')+' · Неактивен — сохранённая запись'
        choices.append(('Сохранённая запись',[(retained.pk,label)]))
    field.choices=choices
