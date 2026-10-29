"""Administrator's company-scoped queue and history of 1C jobs."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_http_methods
from django import forms
from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone
from core.permissions import require_roles
from .models import OneCDiagnosticRun, OneCConnection
from .one_c_schedules import WEEKDAYS

KINDS = [('diagnostic', 'Диагностика структуры'), ('metadata', 'Чтение названий'), ('positions', 'Загрузка должностей'), ('employees', 'Загрузка сотрудников')]


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


@login_required
@require_http_methods(['GET','POST'])
def jobs(request):
    require_roles(request.user, {'ADMIN'})
    from .one_c_schedules import (CATALOGUES, source_ready, last_run, schedule_initial,
                                 first_launch, schedule_label, scheduled_deadline)
    from .one_c import enqueue
    connection,_=OneCConnection.objects.get_or_create(company=request.user.company)
    readiness={item['kind']:source_ready(connection,item['kind']) for item in CATALOGUES}
    schedule_forms={item['kind']:PositionScheduleForm(initial=schedule_initial(connection,item['kind']),auto_id=item['kind']+'_%s') for item in CATALOGUES}
    if request.method=='POST':
        action=request.POST.get('action')
        actions={item['kind']+'_'+command:(item,command) for item in CATALOGUES for command in ('run','schedule')}
        if action not in actions:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        item,command=actions[action]
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
        if form.is_valid():
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
        interval=getattr(connection,kind+'_interval_minutes')
        next_run = scheduled_deadline(connection, kind, now, last)
        import_jobs.append({**item,'ready':readiness[kind],'form':schedule_forms[kind],'interval':interval,
                           'schedule_label':schedule_label(connection,kind),'last':last,'next':next_run,'due':bool(next_run and next_run<=now)})
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
    elif kind == 'metadata':
        query = query.filter(report__metadata_only=True).exclude(report__has_key='positions_import').exclude(report__has_key='employees_import')
    elif kind == 'diagnostic':
        query = query.exclude(report__has_key='positions_import').exclude(report__has_key='employees_import').exclude(report__has_key='metadata_only')
    else:
        kind = ''
    page = Paginator(query, 50).get_page(request.GET.get('page'))
    for run in page:
        run.kind_label = 'Загрузка сотрудников' if run.report.get('employees_import') else 'Загрузка должностей' if run.report.get('positions_import') else 'Чтение названий' if run.report.get('metadata_only') else 'Диагностика структуры'
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'accounts/one_c_jobs.html', {'page': page, 'pending': pending, 'statuses': statuses,
                  'connection':connection,'import_jobs':import_jobs,'schedule_timezone':str(timezone.get_default_timezone()),'busy':bool(connection.active_until and connection.active_until>now),
                  'kinds': KINDS, 'selected_status': status, 'selected_kind': kind, 'params': params.urlencode()})
