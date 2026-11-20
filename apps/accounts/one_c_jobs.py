"""Administrator's company-scoped queue and history of 1C jobs."""
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from django.db.models import Q
from core.permissions import require_roles
from .models import OneCDiagnosticRun, OneCConnection
from .one_c_schedules import WEEKDAYS

KINDS = [('diagnostic', 'Диагностика структуры'), ('uat', 'УАТ: структура и данные'), ('metadata', 'Чтение названий'), ('positions', 'Загрузка должностей'), ('employees', 'Загрузка сотрудников')]
HISTORY_KIND_KEYS = ('uat_import', 'uat_discovery', 'metadata_only', 'employees_import', 'positions_import')


def _sql_identifier(value):
    return '[' + str(value).replace(']', ']]') + ']'


def _sql_literal(value):
    return "N'" + str(value).replace("'", "''") + "'"


def error_help(error, server='', database=''):
    """Return actionable, read-only SQL guidance for a rejected Windows login."""
    if not error:
        return None
    patterns = (
        r'При входе в систему пользователя\s+["«]?([^"»\r\n]+?)["»]?\s+произошла ошибка',
        r"Login failed for user\s+'([^'\r\n]+)'",
    )
    account = next((match.group(1).strip() for pattern in patterns
                    if (match := re.search(pattern, str(error), re.IGNORECASE))), '')
    if not account:
        return None
    account_kind = 'учётной записи компьютера' if account.endswith('$') else 'учётной записи Windows'
    login = _sql_identifier(account)
    literal = _sql_literal(account)
    target = _sql_identifier(database)
    sql = '\n'.join((
        'USE [master];',
        f'IF SUSER_ID({literal}) IS NULL CREATE LOGIN {login} FROM WINDOWS;',
        f'USE {target};',
        f'IF USER_ID({literal}) IS NULL CREATE USER {login} FOR LOGIN {login};',
        f'GRANT CONNECT TO {login};',
        f'GRANT SELECT TO {login};',
    ))
    return {
        'title': 'SQL Server отклонил вход фонового обработчика.',
        'account': account,
        'account_kind': account_kind,
        'machine_account': account.endswith('$'),
        'server': server,
        'database': database,
        'sql': sql,
    }


def history_report_summaries(run_ids):
    """Read only the small JSON fragments rendered by the history table."""
    summaries = {run_id: {} for run_id in run_ids}

    def merge(ids, keys):
        if not ids:
            return
        fields = [f'report__{key}' for key in keys]
        for values in OneCDiagnosticRun.objects.filter(pk__in=ids).values('pk', *fields):
            summaries[values['pk']].update({
                key: values.get(f'report__{key}') for key in keys
                if values.get(f'report__{key}') is not None
            })

    merge(run_ids, HISTORY_KIND_KEYS)
    employee_ids = [pk for pk, report in summaries.items() if report.get('employees_import')]
    position_ids = [pk for pk, report in summaries.items() if report.get('positions_import')]
    uat_ids = [pk for pk, report in summaries.items() if report.get('uat_import') or report.get('uat_discovery')]
    import_ids = {*employee_ids, *position_ids, *uat_ids}
    diagnostic_ids = [pk for pk in run_ids if pk not in import_ids]
    merge(employee_ids, ('employees_result',))
    merge(position_ids, ('positions_result', 'position_source', 'position_flags'))
    merge(uat_ids, ('from_date', 'to_date', 'sync_result'))
    merge(diagnostic_ids, ('metadata_objects_read', 'metadata_errors', 'position_source', 'position_flags'))
    return summaries


class PositionScheduleForm(forms.Form):
    frequency = forms.ChoiceField(label='Периодичность', choices=[('manual', 'Только вручную'),
        ('daily', 'Ежедневно'), ('weekly', 'Еженедельно'), ('interval', 'Через интервал')],
        widget=forms.Select(attrs={'class': 'form-select', 'data-schedule-frequency': ''}))
    time = forms.TimeField(label='Время запуска', required=False, input_formats=['%H:%M'],
        widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': 'form-control', 'step': 60}))
    weekday = forms.TypedChoiceField(label='День недели', required=False, coerce=int,
        choices=WEEKDAYS, widget=forms.Select(attrs={'class': 'form-select'}))
    interval = forms.IntegerField(label='Интервал, минут', required=False, min_value=0, max_value=44640,
        help_text='Минимальный интервал — 15 минут. Например, 60 — каждый час.',
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': 1}))

    def __init__(self, data=None, *args, **kwargs):
        # Keep old API clients working without changing existing interval schedules.
        self.legacy = data is not None and 'frequency' not in data
        if self.legacy:
            data = data.copy()
            data['frequency'] = 'interval' if str(data.get('interval')) != '0' else 'manual'
        super().__init__(data, *args, **kwargs)

    def clean(self):
        values = super().clean()
        frequency = values.get('frequency')
        if frequency != 'manual' and not self.legacy and not values.get('time'):
            self.add_error('time', 'Укажите время запуска.')
        if frequency == 'weekly' and values.get('weekday') not in range(7):
            self.add_error('weekday', 'Выберите день недели.')
        if frequency == 'interval':
            interval = values.get('interval')
            if interval is None:
                self.add_error('interval', 'Укажите интервал.')
            elif interval < 15:
                self.add_error('interval', 'Минимальный интервал — 15 минут.')
        return values


class UatScheduleForm(forms.Form):
    enabled = forms.BooleanField(
        label='Автозагрузка включена', required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )
    time = forms.TimeField(
        label='Время запуска, МСК', input_formats=['%H:%M'],
        widget=forms.TimeInput(format='%H:%M', attrs={'type': 'time', 'class': 'form-control'}),
    )
    lookback = forms.IntegerField(
        label='Повторно читать, дней', min_value=1, max_value=366,
        widget=forms.NumberInput(attrs={'class': 'form-control'}),
    )


@login_required
@require_http_methods(['GET','POST'])
def jobs(request):
    require_roles(request.user, {'ADMIN'})
    from .one_c_schedules import (CATALOGUES, last_run, schedule_initial,
                                 first_launch, schedule_label, scheduled_deadline)
    from .one_c import enqueue
    connection,_=OneCConnection.objects.get_or_create(company=request.user.company)
    diagnostic = connection.runs.filter(
        status='DONE', server=connection.server, database=connection.database,
    ).exclude(report__has_key='positions_import').exclude(report__has_key='employees_import').order_by('-created_at').first()
    from .one_c_employees import source_confirmed as employees_source_confirmed
    from .one_c_positions import source_confirmed as positions_source_confirmed
    readiness = {
        'employees': employees_source_confirmed(connection, diagnostic),
        'positions': positions_source_confirmed(connection, diagnostic),
    }
    schedule_forms={item['kind']:PositionScheduleForm(initial=schedule_initial(connection,item['kind']),auto_id=item['kind']+'_%s') for item in CATALOGUES}
    from .uat_sync import next_scheduled_slot, source_ready as uat_source_ready
    uat_ready = uat_source_ready(connection)
    uat_form = UatScheduleForm(initial={
        'enabled': connection.uat_sync_enabled,
        'time': connection.uat_sync_time,
        'lookback': connection.uat_sync_lookback_days,
    }, auto_id='uat_%s')
    if request.method=='POST':
        action=request.POST.get('action')
        actions={item['kind']+'_'+command:(item,command) for item in CATALOGUES for command in ('run','schedule')}
        if action == 'uat_run':
            if not uat_ready:
                messages.error(request, 'Сначала прочитайте и подтвердите структуру УАТ.')
            else:
                run = enqueue(
                    connection, request.user, database=settings.UAT_DATABASE,
                    uat_import_from=timezone.localdate()-timedelta(days=connection.uat_sync_lookback_days),
                    trust_certificate=True,
                )
                if run and getattr(settings, 'STAGING_CONFIG', None):
                    try:
                        from .uat import launch_staging_worker
                        launch_staging_worker()
                    except (OSError, RuntimeError) as exc:
                        messages.error(request, f'Не удалось запустить обработчик УАТ: {exc}')
                        return redirect('accounts:one_c_jobs')
                messages.success(request, 'Синхронизация УАТ поставлена в очередь.' if run else 'Уже выполняется задание 1С. Дождитесь его окончания и повторите запуск.')
            return redirect('accounts:one_c_jobs')
        if action == 'uat_schedule':
            uat_form = UatScheduleForm(request.POST, auto_id='uat_%s')
            if uat_form.is_valid():
                from apps.audit.models import AuditLog
                from django.db import transaction
                with transaction.atomic():
                    current = OneCConnection.objects.select_for_update().get(pk=connection.pk)
                    old = {
                        'uat_sync_enabled': current.uat_sync_enabled,
                        'uat_sync_time': current.uat_sync_time.strftime('%H:%M'),
                        'uat_sync_lookback_days': current.uat_sync_lookback_days,
                    }
                    current.uat_sync_enabled = uat_form.cleaned_data['enabled']
                    current.uat_sync_time = uat_form.cleaned_data['time']
                    current.uat_sync_lookback_days = uat_form.cleaned_data['lookback']
                    current.save(update_fields=['uat_sync_enabled', 'uat_sync_time', 'uat_sync_lookback_days'])
                    AuditLog.objects.create(
                        user=request.user, action='UPDATE', entity_type='accounts.OneCConnection',
                        entity_id=str(connection.pk), old_value=old,
                        new_value={
                            'uat_sync_enabled': current.uat_sync_enabled,
                            'uat_sync_time': current.uat_sync_time.strftime('%H:%M'),
                            'uat_sync_lookback_days': current.uat_sync_lookback_days,
                        }, comment='Расписание синхронизации УАТ',
                    )
                messages.success(request, 'Расписание синхронизации УАТ сохранено.')
                return redirect('accounts:one_c_jobs')
            # Keep rendering the invalid UAT form together with the complete job page.
        elif action not in actions:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        if action == 'uat_schedule':
            # Validation errors are rendered below; catalogue actions are skipped.
            item = command = None
        else:
            item,command=actions[action]
        if item is not None:
            kind=item['kind'];label=item['label']
            unconfirmed=f'Сначала подтвердите источник {label} диагностикой текущего подключения.'
            if command=='run':
                if not readiness[kind]:messages.error(request,unconfirmed)
                else:
                    run=enqueue(connection,request.user,**{kind:True})
                    messages.success(request,f'Загрузка {label} поставлена в очередь.' if run else 'Уже выполняется задание 1С. Дождитесь его окончания и повторите запуск.')
                return redirect('accounts:one_c_jobs')
            form=PositionScheduleForm(request.POST,auto_id=kind+'_%s')
            schedule_forms[kind]=form
        if item is not None and form.is_valid():
            frequency = form.cleaned_data['frequency']
            interval = {'manual': 0, 'daily': 1440, 'weekly': 10080}.get(frequency, form.cleaned_data['interval'])
            if interval and not readiness[kind]:form.add_error(None,unconfirmed)
            else:
                from apps.audit.models import AuditLog
                from django.db import transaction
                field=kind+'_interval_minutes'
                schedule_field = kind + '_schedule'
                with transaction.atomic():
                    current=OneCConnection.objects.select_for_update().get(pk=connection.pk)
                    old = {field: getattr(current, field)}
                    previous_schedule = getattr(current, schedule_field)
                    schedule = {}
                    if not form.legacy and interval:
                        clock = form.cleaned_data['time']
                        weekday = form.cleaned_data['weekday'] if frequency == 'weekly' else 0
                        schedule = {'frequency': frequency, 'time': clock.strftime('%H:%M'), 'weekday': weekday}
                        if (all(previous_schedule.get(key) == value for key, value in schedule.items())
                                and getattr(current, field) == interval and previous_schedule.get('starts_at')):
                            schedule['starts_at'] = previous_schedule['starts_at']
                        else:
                            schedule['starts_at'] = first_launch(frequency, clock, weekday, timezone.now()).isoformat()
                    setattr(current,field,interval)
                    setattr(current, schedule_field, schedule)
                    new = {field: interval}
                    if schedule != previous_schedule:
                        old[schedule_field] = previous_schedule
                        new[schedule_field] = schedule
                    current.save(update_fields=[field, schedule_field])
                    AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='accounts.OneCConnection',entity_id=str(connection.pk),old_value=old,new_value=new,comment=f'Расписание загрузки {label} из 1С')
                messages.success(request,f'Расписание загрузки {label} сохранено.' if interval else f'Автоматическая загрузка {label} отключена.')
                return redirect('accounts:one_c_jobs')
    now=timezone.now()
    import_jobs=[]
    for item in CATALOGUES:
        kind=item['kind'];last=last_run(connection,kind)
        if last:
            last.error_help=error_help(last.error,last.server,last.database)
        interval=getattr(connection,kind+'_interval_minutes')
        next_run = scheduled_deadline(connection, kind, now, last)
        import_jobs.append({**item,'ready':readiness[kind],'form':schedule_forms[kind],'interval':interval,
                           'schedule_label':schedule_label(connection,kind),'last':last,'next':next_run,'due':bool(next_run and next_run<=now)})
    uat_last = connection.runs.filter(
        database=settings.UAT_DATABASE, report__uat_import=True,
    ).order_by('-created_at', '-pk').first()
    if uat_last:
        uat_last.error_help = error_help(uat_last.error, uat_last.server, uat_last.database)
    uat_job = {
        'ready': uat_ready, 'form': uat_form, 'last': uat_last,
        'next': next_scheduled_slot(connection, now) if connection.uat_sync_enabled else None,
        'database': settings.UAT_DATABASE,
    }
    query = OneCDiagnosticRun.objects.filter(connection__company=request.user.company).select_related('created_by').order_by('-created_at', '-pk')
    pending = query.filter(status__in=['QUEUED', 'RUNNING']).exists()
    status = request.GET.get('status', '')
    statuses = OneCDiagnosticRun._meta.get_field('status').choices
    if status in dict(statuses):
        query = query.filter(status=status)
    else:
        status = ''
    kind = request.GET.get('kind', '')
    if kind == 'employees':
        query = query.filter(report__employees_import=True)
    elif kind == 'positions':
        query = query.filter(report__positions_import=True)
    elif kind == 'uat':
        query = query.filter(Q(report__uat_discovery=True) | Q(report__uat_import=True))
    elif kind == 'metadata':
        query = query.filter(report__metadata_only=True).exclude(report__has_key='positions_import').exclude(report__has_key='employees_import')
    elif kind == 'diagnostic':
        query = query.exclude(report__has_key='positions_import').exclude(report__has_key='employees_import').exclude(report__has_key='metadata_only').exclude(report__has_key='uat_discovery').exclude(report__has_key='uat_import')
    else:
        kind = ''
    page = Paginator(query.defer('report'), 50).get_page(request.GET.get('page'))
    summaries = history_report_summaries([run.pk for run in page.object_list])
    for run in page:
        # Keep the template API stable without fetching the complete JSON field.
        # In particular, old UAT runs may contain thousands of source documents.
        run.report = summaries.get(run.pk, {})
        run.kind_label = 'Загрузка данных УАТ' if run.report.get('uat_import') else 'Загрузка сотрудников' if run.report.get('employees_import') else 'Загрузка должностей' if run.report.get('positions_import') else 'Диагностика УАТ' if run.report.get('uat_discovery') else 'Чтение названий' if run.report.get('metadata_only') else 'Диагностика структуры'
        run.system_label = 'УАТ 1С' if run.report.get('uat_import') or run.report.get('uat_discovery') else 'ЗУП 1С'
        run.error_help=error_help(run.error,run.server,run.database)
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'accounts/one_c_jobs.html', {'page': page, 'pending': pending, 'statuses': statuses,
                  'connection':connection,'import_jobs':import_jobs,'uat_job':uat_job,'schedule_timezone':str(timezone.get_default_timezone()),'busy':bool(connection.active_until and connection.active_until>now),
                  'kinds': KINDS, 'selected_status': status, 'selected_kind': kind, 'params': params.urlencode()})
