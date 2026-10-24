"""Administrator's company-scoped queue and history of 1C jobs."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from datetime import timedelta
from core.permissions import require_roles
from .models import OneCDiagnosticRun, OneCConnection

KINDS = [('diagnostic', 'Диагностика структуры'), ('metadata', 'Чтение названий'), ('positions', 'Загрузка должностей')]


class PositionScheduleForm(forms.Form):
    interval=forms.IntegerField(label='Повторять каждые N минут',min_value=0,max_value=44640,help_text='0 — только вручную. Минимальный интервал — 15 минут. Например: 60 — каждый час, 1440 — раз в сутки.')

    def clean_interval(self):
        value=self.cleaned_data['interval']
        if 0<value<15:raise forms.ValidationError('Минимальный интервал — 15 минут.')
        return value


@login_required
@require_http_methods(['GET','POST'])
def jobs(request):
    require_roles(request.user, {'ADMIN'})
    from .one_c_positions import source_confirmed
    from .one_c import enqueue
    connection,_=OneCConnection.objects.get_or_create(company=request.user.company)
    ready=source_confirmed(connection)
    schedule_form=PositionScheduleForm(initial={'interval':connection.positions_interval_minutes})
    if request.method=='POST':
        action=request.POST.get('action')
        if action=='positions_run':
            if not ready:messages.error(request,'Сначала подтвердите справочник должностей диагностикой текущего подключения.')
            else:
                run=enqueue(connection,request.user,positions=True)
                messages.success(request,'Загрузка должностей поставлена в очередь.' if run else 'Уже выполняется задание 1С. Дождитесь его окончания и повторите запуск.')
            return redirect('accounts:one_c_jobs')
        elif action=='positions_schedule':
            schedule_form=PositionScheduleForm(request.POST)
            if schedule_form.is_valid():
                interval=schedule_form.cleaned_data['interval']
                if interval and not ready:schedule_form.add_error(None,'Сначала подтвердите справочник должностей диагностикой текущего подключения.')
                else:
                    from apps.audit.models import AuditLog
                    from django.db import transaction
                    with transaction.atomic():
                        old=connection.positions_interval_minutes
                        connection.positions_interval_minutes=interval
                        connection.save(update_fields=['positions_interval_minutes'])
                        AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='accounts.OneCConnection',entity_id=str(connection.pk),old_value={'positions_interval_minutes':old},new_value={'positions_interval_minutes':interval},comment='Расписание загрузки должностей из 1С')
                    messages.success(request,'Расписание загрузки должностей сохранено.' if interval else 'Автоматическая загрузка должностей отключена.')
                    return redirect('accounts:one_c_jobs')
        else:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
    last_positions=connection.runs.filter(report__positions_import=True,server=connection.server,database=connection.database).order_by('-created_at').first()
    next_positions=None
    if connection.positions_interval_minutes:
        next_positions=last_positions.created_at+timedelta(minutes=connection.positions_interval_minutes) if last_positions else timezone.now()
    query = OneCDiagnosticRun.objects.filter(connection__company=request.user.company).select_related('created_by').order_by('-created_at', '-pk')
    pending = query.filter(status__in=['QUEUED', 'RUNNING']).exists()
    status = request.GET.get('status', '')
    statuses = OneCDiagnosticRun._meta.get_field('status').choices
    if status in dict(statuses):
        query = query.filter(status=status)
    else:
        status = ''
    kind = request.GET.get('kind', '')
    if kind == 'positions':
        query = query.filter(report__positions_import=True)
    elif kind == 'metadata':
        query = query.filter(report__metadata_only=True).exclude(report__has_key='positions_import')
    elif kind == 'diagnostic':
        query = query.exclude(report__has_key='positions_import').exclude(report__has_key='metadata_only')
    else:
        kind = ''
    page = Paginator(query, 50).get_page(request.GET.get('page'))
    for run in page:
        run.kind_label = 'Загрузка должностей' if run.report.get('positions_import') else 'Чтение названий' if run.report.get('metadata_only') else 'Диагностика структуры'
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'accounts/one_c_jobs.html', {'page': page, 'pending': pending, 'statuses': statuses,
                  'connection':connection,'positions_ready':ready,'schedule_form':schedule_form,'last_positions':last_positions,'next_positions':next_positions,'positions_due':bool(next_positions and next_positions<=timezone.now()),'busy':bool(connection.active_until and connection.active_until>timezone.now()),
                  'kinds': KINDS, 'selected_status': status, 'selected_kind': kind, 'params': params.urlencode()})
