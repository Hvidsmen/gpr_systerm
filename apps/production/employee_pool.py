"""Eligible employees; dated camp presence affects presentation, not eligibility."""
from django.db.models import Q
from apps.resources.models import Employee
from apps.personnel.models import CampStay,StaffingSlot
from apps.personnel.housing import present_stays
from .dispatch_models import EmployeeActivity

GENERAL_PATH='employee__position__planning_mapping__brigade__group__is_general_staff'


def eligible_employees(obj,day):
    """Active staff plus anyone already recorded on this fact day."""
    recorded=EmployeeActivity.objects.filter(company=obj.company,date=day).values('employee_id')
    return Employee.objects.filter(company=obj.company).filter(Q(is_active=True)|Q(pk__in=recorded))


def can_add_employee(employee,obj,day):
    return employee.is_active or EmployeeActivity.objects.filter(company=obj.company,date=day,employee=employee).exists()


def activity_direction(row):
    if row.entry_direction:return row.entry_direction
    code=row.reported_by.role.code if row.reported_by_id and row.reported_by.role_id else None
    if code=='DISPATCHER':return 'works'
    if code=='PERSONNEL_DISPATCHER':return 'labor'
    from .production_entry import is_general_employee
    return 'labor' if is_general_employee(row.employee) else 'works'


def labor_direction():
    return Q(entry_direction='labor')|(Q(entry_direction='')&(Q(reported_by__role__code='PERSONNEL_DISPATCHER')|(Q(**{GENERAL_PATH:True})&~Q(reported_by__role__code='DISPATCHER'))))


def prepare(obj,day,employee_ids=None):
    employees_query=eligible_employees(obj,day)
    if employee_ids is not None:employees_query=employees_query.filter(pk__in=employee_ids)
    employees=list(employees_query.select_related('position__planning_mapping__brigade__group').order_by('last_name','first_name','middle_name','employee_number','pk'))
    ids={employee.pk for employee in employees}
    stays={s.person.employee_id:s for s in present_stays(obj.company,day).filter(person__employee_id__in=ids).exclude(person__employee=None).select_related('camp','person__employee')}
    linked=set(obj.camp_links.filter(company=obj.company).values_list('camp_id',flat=True))
    # Closed actual stays take precedence over an undated legacy table status.
    last={}
    for stay in CampStay.objects.filter(company=obj.company,cancelled_at=None,arrived_on__lte=day,person__employee_id__in=ids).select_related('person').order_by('-arrived_on','-pk'):
        last.setdefault(stay.person.employee_id,stay)
    legacy={}
    for slot in StaffingSlot.objects.filter(company=obj.company,person__employee_id__in=ids).values('person__employee_id','status'):
        legacy.setdefault(slot['person__employee_id'],set()).add(slot['status'])
    from apps.resources.group_order import employee_order
    employees.sort(key=employee_order)
    locations={};groups=[[],[],[]]
    for employee in employees:
        stay=stays.get(employee.pk);previous=last.get(employee.pk)
        mapping=getattr(employee.position,'planning_mapping',None) if employee.position_id else None
        staffing_group=(mapping.brigade.group.name if mapping and mapping.brigade.group_id else 'Без группы')
        group=0 if stay and stay.camp_id in linked else 1 if stay else 2
        rest=bool(not stay and (previous and previous.departed_on and previous.departed_on<=day or not previous and legacy.get(employee.pk)=={'МО'}))
        text=(('ВЖГ: '+stay.camp.name) if stay else 'Нахождение в ВЖГ не оформлено'+(' · межвахтовый отдых' if rest else ''))+(' · архив — уже есть в факте дня' if not employee.is_active else '')
        locations[str(employee.pk)]={'text':text,'camp':stay.camp.name if stay else '', 'camp_id':stay.camp_id if stay else None,'group':group,'rest':rest,'archived':not employee.is_active,'position':employee.position_id,'name':employee.display_name,'number':employee.employee_number,'position_label':employee.display_position,'staff_group':staffing_group}
        label=' · '.join(filter(None,[employee.display_name,'№ '+employee.employee_number if employee.employee_number else '',employee.display_position or 'Должность не указана',text]))
        groups[group].append((employee.pk,label))
    return {'locations':locations,'groups':[(label,values) for label,values in zip(['ВЖГ, связанные с объектом','Другие ВЖГ','Без оформленного нахождения в ВЖГ'],groups) if values]}


def apply(field,pool,retained=None):
    choices=[('',field.empty_label or 'Выберите сотрудника'),*pool['groups']]
    if retained and not retained.is_active:
        label=retained.display_name+(' · № '+retained.employee_number if retained.employee_number else '')+' · Неактивен — сохранённая запись'
        choices.append(('Сохранённая запись',[(retained.pk,label)]))
    field.choices=choices
