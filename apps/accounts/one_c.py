"""Administrator-only 1C diagnostics; SQL is executed by the Windows worker."""
import json
import os
import subprocess
import uuid
from datetime import timedelta
from pathlib import Path
from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from core.permissions import require_roles
from .models import OneCConnection, OneCDiagnosticRun


class ConnectionForm(forms.ModelForm):
    class Meta:
        model = OneCConnection
        fields = ['server', 'database', 'trust_certificate', 'diagnostic_interval_minutes']
        labels = {'server':'Сервер MS SQL', 'database':'База 1С', 'trust_certificate':'Доверять сертификату SQL без проверки', 'diagnostic_interval_minutes':'Диагностика каждые N минут (0 — только вручную)'}

    def clean_server(self):
        import re
        value=self.cleaned_data['server']
        if not re.fullmatch(r'[a-zA-Z0-9_.\\,\-]+',value):raise ValidationError('Укажите имя сервера, экземпляр или порт.')
        return value

    def clean_database(self):
        import re
        value=self.cleaned_data['database']
        if not re.fullmatch(r'[\w\-]+',value):raise ValidationError('Недопустимое имя базы.')
        return value

    def clean_diagnostic_interval_minutes(self):
        value=self.cleaned_data['diagnostic_interval_minutes']
        if value and value<15:raise ValidationError('Минимальный интервал — 15 минут.')
        return value


def enqueue(connection, actor=None):
    # Conditional update is the cross-process claim, including SQLite deployments.
    now=timezone.now()
    from django.db import transaction
    with transaction.atomic():
        claimed=OneCConnection.objects.filter(pk=connection.pk).filter(active_until__isnull=True).update(active_until=now+timedelta(minutes=20))
        if not claimed:return None
        return OneCDiagnosticRun.objects.create(connection=connection,created_by=actor,server=connection.server,database=connection.database,trust_certificate=connection.trust_certificate)


def process_queue():
    now=timezone.now()
    stale=OneCConnection.objects.filter(active_until__lt=now)
    OneCDiagnosticRun.objects.filter(connection__in=stale,status__in=['QUEUED','RUNNING']).update(status='ERROR',error='Запуск прерван или превышено время ожидания.',finished_at=now)
    stale.update(active_until=None)
    for connection in OneCConnection.objects.filter(diagnostic_interval_minutes__gt=0,active_until__isnull=True):
        last=connection.runs.order_by('-created_at').first()
        if last is None or last.created_at+timedelta(minutes=connection.diagnostic_interval_minutes)<=now:enqueue(connection)
    for run in OneCDiagnosticRun.objects.filter(status='QUEUED').order_by('created_at'):
        if not OneCDiagnosticRun.objects.filter(pk=run.pk,status='QUEUED').update(status='RUNNING'):continue
        try:
            if os.name!='nt':raise RuntimeError('Диагностика MS SQL с Windows-аутентификацией доступна на Windows-сервере портала.')
            root=Path(settings.CONFIG_DIR)
            command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(Path(settings.BASE_DIR)/'ops/diagnose-1c.ps1'),'-Root',str(root),'-RunHere','-Server',run.server,'-Database',run.database,'-RunId',run.pk.hex]
            if run.trust_certificate:command.append('-TrustServerCertificate')
            subprocess.run(command,timeout=900,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,creationflags=subprocess.CREATE_NO_WINDOW)
            directory=root/'.runtime/1c-diagnostics'/run.pk.hex
            status=json.loads((directory/'status.json').read_text(encoding='utf-8-sig'))
            if not status.get('ok'):raise RuntimeError(status.get('error','Ошибка диагностики.'))
            run.report=json.loads((directory/'report.json').read_text(encoding='utf-8-sig'))
            run.status='DONE'
        except Exception as exc:
            run.status='ERROR';run.error=str(exc)[:4000]
        finally:
            run.finished_at=timezone.now();run.save(update_fields=['status','error','report','finished_at'])
            OneCConnection.objects.filter(pk=run.connection_id).update(active_until=None)


@login_required
def integration(request):
    require_roles(request.user,{'ADMIN'})
    connection,_=OneCConnection.objects.get_or_create(company_id=request.user.company_id)
    form=ConnectionForm(instance=connection)
    if request.method=='POST':
        if request.POST.get('action')=='save':
            form=ConnectionForm(request.POST,instance=connection)
            if form.is_valid():
                connection=form.save(commit=False)
                connection.save(update_fields=list(form.fields))
                messages.success(request,'Настройки 1С сохранены.')
                return redirect('accounts:one_c')
        elif request.POST.get('action')=='diagnose':
            run=enqueue(connection,request.user)
            messages.success(request,'Диагностика поставлена в очередь. Фоновый обработчик начнёт её в течение минуты.' if run else 'Диагностика уже ожидает запуска или выполняется.')
            return redirect('accounts:one_c')
    runs=list(connection.runs.order_by('-created_at')[:20])
    return render(request,'accounts/one_c.html',{'form':form,'connection':connection,'runs':runs,'latest':runs[0] if runs else None,'pending':any(r.status in {'QUEUED','RUNNING'} for r in runs)})
