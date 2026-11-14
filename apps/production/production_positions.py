"""A selected SMG role changes accounting, never the employee's staffing role."""
from collections import defaultdict
from apps.resources.models import Brigade,Position
from apps.resources.position_catalog import position_for
from .dispatch_models import EmployeeActivity


def plan_positions(obj,day):
    from .dispatch import plans
    expected=plans(obj,day)[1]['labor']
    roles={}
    from apps.resources.group_order import ordered
    for brigade in ordered(Brigade.objects.filter(company=obj.company,pk__in=expected).select_related('group'),group_path='group'):
        roles[position_for(brigade).pk]=(brigade,expected[brigade.pk])
    return roles


def context(obj,day):
    from apps.resources.models import Employee
    roles=plan_positions(obj,day);counts=defaultdict(set);saved=[]
    rows=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day).select_related(
        'position','planning_position','employee__position__planning_mapping__brigade__group')
    for row in rows:
        role=row.planning_position_id or row.position_id
        if role:counts[role].add(row.employee_id)
        staff_position=row.employee.position
        mapping=getattr(staff_position,'planning_mapping',None) if staff_position else None
        staff_group=mapping.brigade.group.name if mapping and mapping.brigade.group_id else 'Без группы'
        saved.append({'id':row.pk,'employee':row.employee_id,'role':role,
            'staff_group':staff_group,'staff_position':row.employee.display_position or 'Должность не указана'})
    from django.db.models import Q
    employees=Employee.objects.filter(company=obj.company).filter(Q(is_active=True)|Q(pk__in=[row['employee'] for row in saved])).values_list('pk','position_id')
    totals={'plan':sum(count for _,count in roles.values()),'assigned':len({row['employee'] for row in saved}),'remaining':sum(max(0,count-len(counts[pk])) for pk,(_,count) in roles.items()),'unplanned':len({row['employee'] for row in saved if row['role'] not in roles})}
    from apps.resources.group_order import labor_group_order
    position_plan=[{'id':pk,'name':brigade.name,'group':brigade.group.name if brigade.group_id else 'Без группы плана','group_id':brigade.group_id or 0,'group_order':labor_group_order(brigade.group if brigade.group_id else None)[0],'plan':count,'actual':len(counts[pk]),'remaining':max(0,count-len(counts[pk]))} for pk,(brigade,count) in roles.items()]
    grouped={}
    for row in position_plan:
        group=grouped.setdefault((row['group_order'],row['group_id'],row['group']),{'name':row['group'],'order':row['group_order'],'roles':[],'plan':0,'actual_people':set(),'remaining':0})
        group['roles'].append(row);group['plan']+=row['plan'];group['remaining']+=row['remaining']
        group['actual_people'].update(counts[row['id']])
    position_groups=[]
    for group in grouped.values():
        group['actual']=len(group.pop('actual_people'));position_groups.append(group)
    position_groups.sort(key=lambda group:(group['order'],group['name'].casefold()))
    return {'position_totals':totals,'position_plan':position_plan,'position_groups':position_groups,
        'position_plan_saved':saved,'position_employee_roles':dict(employees)}
