from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.shortcuts import render,get_object_or_404,redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from core.fact_edit_window import window_context,require_fact_day
from core.permissions import require_roles,role_code
from . import transport
from .transport_views import Selector
from .transport_details_models import EquipmentDayDetails,DETAIL_FIELDS
from apps.resources.models import Equipment


from .transport_detail_forms import DetailsForm,EMPLOYEE_FIELDS


@login_required
@require_http_methods(['GET','POST'])
def details(request,pk):
    require_roles(request.user,transport.OPERATORS if request.method=='POST' else transport.READERS)
    machine=get_object_or_404(Equipment,company=request.user.company,pk=pk)
    selector=Selector(request.POST if request.method=='POST' else request.GET,user=request.user)
    if not selector.is_valid():
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('Укажите ВЖГ и дату.')
    camp,day=selector.cleaned_data['camp'],selector.cleaned_data['date'];transport.authorize(request.user,camp,False)
    if not transport.present(request.user.company,day).filter(equipment=machine,camp=camp).exists():
        from django.http import Http404
        raise Http404('Машина не находится в выбранном ВЖГ на эту дату.')
    current=EquipmentDayDetails.objects.filter(company=request.user.company,equipment=machine,date=day).first()
    previous=EquipmentDayDetails.objects.filter(company=request.user.company,equipment=machine,date__lt=day).first()
    initial={name:getattr(previous,name) for name,_ in DETAIL_FIELDS if name not in EMPLOYEE_FIELDS} if previous and not current else {}
    initial['signed']=transport.snapshot(request.user,camp,day)
    form=DetailsForm(request.POST if request.method=='POST' else None,instance=current,initial=initial,company=request.user.company)
    if request.method=='POST':
        try:require_fact_day(request.user,day)
        except ValidationError as exc:raise PermissionDenied('; '.join(exc.messages))
        if form.is_valid():
            try:
                with transaction.atomic():
                    transport.authorize(request.user,camp)
                    versions=transport._payload(request.user,camp,day,form.cleaned_data['signed'])
                    machines,controls=transport._lock(request.user,[machine.pk])
                    transport._check(request.user,machines[0],controls[machine.pk],versions,None)
                    if not transport.present(request.user.company,day).filter(equipment=machine,camp=camp).exists():raise ValidationError('Размещение машины изменилось. Обновите страницу.')
                    if any(form.cleaned_data.get(name) for name in EMPLOYEE_FIELDS) and not machine.transport_days.filter(company=request.user.company,date=day,status='WORKED').exists():raise ValidationError('Сотрудники смен относятся к работе за день. Сначала отметьте, что техника работала за выбранную дату.')
                    row=form.save(commit=False);row.company=request.user.company;row.equipment=machine;row.camp=camp;row.date=day
                    if not row.pk:row.recorded_by=request.user
                    row.full_clean();row.save();transport._bump(controls[machine.pk]);require_fact_day(request.user,day)
            except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
            else:
                messages.success(request,'Сведения по технике сохранены с историей изменений.')
                return redirect(reverse('production:transport_details',args=[machine.pk])+'?'+urlencode({'camp':camp.pk,'date':str(day)}))
    window=window_context(request.user,day)
    return render(request,'production/transport_details.html',dict(machine=machine,camp=camp,day=day,form=form,
        editable=window['fact_day_editable'] and role_code(request.user) in transport.OPERATORS,details=current or previous,
        carried=bool(previous and not current),query=urlencode({'camp':camp.pk,'date':str(day)}),**window),status=400 if form.errors else 200)
