from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q,Case,When,Value,IntegerField,Exists,OuterRef
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from core.permissions import FACT_ROLES, require_roles
from apps.personnel.models import CampStay
from .dispatch_models import EmployeeActivity
from .forms import ObjectDateForm
from .employee_pool import prepare,eligible_employees


@login_required
@require_GET
def search(request):
    require_roles(request.user,FACT_ROLES)
    selector=ObjectDateForm(request.GET,user=request.user)
    if not selector.is_valid():return JsonResponse({'error':'Выберите доступный объект и корректную дату.'},status=400)
    obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
    query=eligible_employees(obj,day).select_related('position')
    terms=[term for term in request.GET.get('q','').strip().split() if term][:6]
    for term in terms:
        query=query.filter(Q(last_name__icontains=term)|Q(first_name__icontains=term)|Q(middle_name__icontains=term)|
            Q(employee_number__icontains=term)|Q(position__name__icontains=term)|Q(personnel_card__stays__camp__name__icontains=term))
    position=request.GET.get('position');shift=request.GET.get('shift');blocked=[]
    if shift and shift.isdigit() and 1<=int(shift)<=24:
        occupied=EmployeeActivity.objects.filter(company=obj.company,date=day,shift=int(shift),employee__in=query).select_related('employee','construction_object').order_by('employee__last_name','employee__first_name','pk')
        if terms:
            blocked=[{'id':row.employee_id,'name':row.employee.display_name,'activity':row.pk,'construction_object':row.construction_object_id,
                'construction_object_name':str(row.construction_object),'shift':row.shift} for row in occupied[:10]]
        query=query.exclude(pk__in=occupied.values('employee_id'))
    present=CampStay.objects.filter(company=obj.company,cancelled_at=None,arrived_on__lte=day,person__employee_id=OuterRef('pk')).filter(Q(departed_on=None)|Q(departed_on__gt=day))
    linked=present.filter(camp__object_links__company=obj.company,camp__object_links__construction_object=obj)
    query=query.annotate(
        _smg_priority=Case(When(position_id=int(position),then=Value(0)),default=Value(1),output_field=IntegerField()) if position and position.isdigit() else Value(0,output_field=IntegerField()),
        _smg_missing=Case(When(position__planning_mapping__isnull=False,then=Value(0)),default=Value(1),output_field=IntegerField()),
        _linked_vjg=Exists(linked),_any_vjg=Exists(present),
    ).annotate(_vjg_priority=Case(When(_linked_vjg=True,then=Value(0)),When(_any_vjg=True,then=Value(1)),default=Value(2),output_field=IntegerField()))
    query=query.distinct().order_by('_smg_priority','_smg_missing','position__planning_mapping__brigade__name','_vjg_priority','last_name','first_name','middle_name','employee_number','pk')
    page=Paginator(query,50).get_page(request.GET.get('page'))
    pool=prepare(obj,day,[employee.pk for employee in page.object_list])
    locations=pool['locations']
    items=[]
    for employee in page.object_list:
        info=locations[str(employee.pk)]
        label=' · '.join(filter(None,[employee.display_name,'№ '+employee.employee_number if employee.employee_number else '',employee.display_position or 'Должность не указана',info['text']]))
        items.append({'id':employee.pk,'label':label,**info})
    return JsonResponse({'items':items,'blocked':blocked,'page':page.number,'pages':page.paginator.num_pages,'total':page.paginator.count,'has_next':page.has_next()})
