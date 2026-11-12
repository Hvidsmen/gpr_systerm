from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from core.permissions import FACT_ROLES, require_roles
from apps.resources.models import Employee
from .forms import ObjectDateForm
from .employee_pool import prepare


@login_required
@require_GET
def search(request):
    require_roles(request.user,FACT_ROLES)
    selector=ObjectDateForm(request.GET,user=request.user)
    if not selector.is_valid():return JsonResponse({'error':'Выберите доступный объект и корректную дату.'},status=400)
    obj=selector.cleaned_data['construction_object'];day=selector.cleaned_data['date']
    query=Employee.objects.filter(company=obj.company,is_active=True).select_related('position')
    terms=[term for term in request.GET.get('q','').strip().split() if term][:6]
    for term in terms:
        query=query.filter(Q(last_name__icontains=term)|Q(first_name__icontains=term)|Q(middle_name__icontains=term)|
            Q(employee_number__icontains=term)|Q(position__name__icontains=term)|Q(personnel_card__stays__camp__name__icontains=term))
    position=request.GET.get('position')
    if position and position.isdigit():query=query.filter(position_id=position)
    query=query.distinct().order_by('last_name','first_name','middle_name','employee_number','pk')
    page=Paginator(query,50).get_page(request.GET.get('page'))
    pool=prepare(obj,day,[employee.pk for employee in page.object_list])
    locations=pool['locations']
    items=[]
    for employee in page.object_list:
        info=locations[str(employee.pk)]
        items.append({'id':employee.pk,**info})
    return JsonResponse({'items':items,'page':page.number,'pages':page.paginator.num_pages,'total':page.paginator.count,'has_next':page.has_next()})
