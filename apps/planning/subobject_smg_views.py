from django.contrib import messages
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404,redirect
from core.permissions import require_roles,PLAN_ROLES,scope_queryset
from .models import DailySchedule,DailyScheduleEntry
from apps.works.models import ProjectWork
from .subobject_smg import map_entry


@login_required
@require_POST
def map_work(request,pk):
    require_roles(request.user,PLAN_ROLES)
    schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
    try:map_entry(request.user,schedule,int(request.POST.get('entry_id','')),int(request.POST.get('work_id','')));messages.success(request,'Работа связана с общим ГПР. Объёмы СМГ сохранены.')
    except (ValueError,TypeError,DailyScheduleEntry.DoesNotExist,ProjectWork.DoesNotExist):messages.error(request,'Выберите доступную работу этого субобъекта и работу его общего ГПР.')
    except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return redirect('planning:daily_schedule_detail',pk=schedule.pk)
