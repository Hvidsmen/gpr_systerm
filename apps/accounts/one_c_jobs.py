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

KINDS = [('diagnostic', 'Диагностика структуры'), ('metadata', 'Чтение названий'), ('positions', 'Загрузка должностей'), ('employees', 'Загрузка сотрудников')]


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
    from .one_c_schedules import CATALOGUES, source_ready, last_run
    from .one_c import enqueue
    connection,_=OneCConnection.objects.get_or_create(company=request.user.company)
    readiness={item['kind']:source_ready(connection,item['kind']) for item in CATALOGUES}
    schedule_forms={item['kind']:PositionScheduleForm(initial={'interval':getattr(connection,item['kind']+'_interval_minutes')},auto_id=item['kind']+'_%s') for item in CATALOGUES}
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
            interval=form.cleaned_data['interval']
            if interval and not readiness[kind]:form.add_error(None,unconfirmed)
            else:
                from apps.audit.models import AuditLog
                from django.db import transaction
                field=kind+'_interval_minutes'
                with transaction.atomic():
                    current=OneCConnection.objects.select_for_update().get(pk=connection.pk)
                    old=getattr(current,field)
                    setattr(current,field,interval)
                    current.save(update_fields=[field])
                    AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='accounts.OneCConnection',entity_id=str(connection.pk),old_value={field:old},new_value={field:interval},comment=f'Расписание загрузки {label} из 1С')
                messages.success(request,f'Расписание загрузки {label} сохранено.' if interval else f'Автоматическая загрузка {label} отключена.')
                return redirect('accounts:one_c_jobs')
    now=timezone.now()
    import_jobs=[]
    for item in CATALOGUES:
        kind=item['kind'];last=last_run(connection,kind)
        interval=getattr(connection,kind+'_interval_minutes')
        next_run=last.created_at+timedelta(minutes=interval) if last and interval else now if interval else None
        import_jobs.append({**item,'ready':readiness[kind],'form':schedule_forms[kind],'interval':interval,'last':last,'next':next_run,'due':bool(next_run and next_run<=now)})
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
                  'connection':connection,'import_jobs':import_jobs,'busy':bool(connection.active_until and connection.active_until>now),
                  'kinds': KINDS, 'selected_status': status, 'selected_kind': kind, 'params': params.urlencode()})
