"""A selected SMG role changes accounting, never the employee's staffing role."""
from collections import defaultdict
from apps.resources.models import Brigade,Position
from apps.resources.position_catalog import position_for
from .dispatch_models import EmployeeActivity


def plan_positions(obj,day):
    from .dispatch import plans
    expected=plans(obj,day)[1]['labor']
    roles={}
    for brigade in Brigade.objects.filter(company=obj.company,pk__in=expected).order_by('name','pk'):
        roles[position_for(brigade).pk]=(brigade,expected[brigade.pk])
    return roles


def context(obj,day):
    from apps.resources.models import Employee
    roles=plan_positions(obj,day);counts=defaultdict(set);saved=[]
    for row in EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day).select_related('position','planning_position'):
        role=row.planning_position_id or row.position_id
        if role:counts[role].add(row.employee_id)
        saved.append({'id':row.pk,'employee':row.employee_id,'role':role})
    from django.db.models import Q
    employees=Employee.objects.filter(company=obj.company).filter(Q(is_active=True)|Q(pk__in=[row['employee'] for row in saved])).values_list('pk','position_id')
    return {'position_plan':[{'id':pk,'name':brigade.name,'plan':count,'actual':len(counts[pk]),'remaining':max(0,count-len(counts[pk]))} for pk,(brigade,count) in roles.items()],
        'position_plan_saved':saved,'position_employee_roles':dict(employees)}
