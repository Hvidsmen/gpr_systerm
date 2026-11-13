"""Small full-day aggregates used by the paged production editor."""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET
from core.permissions import READ_ROLES, require_roles
from .dispatch import plans
from .dispatch_models import EmployeeActivity
from .forms import ObjectDateForm
from .models import DailyFact


@login_required
@require_GET
@never_cache
def summary(request):
    require_roles(request.user, READ_ROLES | {'DISPATCHER', 'ALL_FACT_DISPATCHER'})
    selector=ObjectDateForm(request.GET,user=request.user,production_scope=True)
    if not selector.is_valid():return JsonResponse({'error':'Выберите доступный объект и дату.'},status=400)
    obj,day=selector.cleaned_data['construction_object'],selector.cleaned_data['date']
    work_plan,_=plans(obj,day)
    planned={key for key,value in work_plan.items() if value>0}
    facts=DailyFact.objects.filter(company=obj.company,date=day,project_work__section__construction_object=obj)
    fact_keys=set(facts.values_list('project_work_id','work_item_id'))
    employees=EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day,entry_direction='works').values('employee_id').distinct().count()
    return JsonResponse({'with_fact':facts.count(),'missing':len(planned-fact_keys),'unplanned':len(fact_keys-set(work_plan)),'employees':employees})
