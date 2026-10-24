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


def enqueue(connection, actor=None, metadata_only=False):
    # Conditional update is the cross-process claim, including SQLite deployments.
    now=timezone.now()
    from django.db import transaction
    with transaction.atomic():
        claimed=OneCConnection.objects.filter(pk=connection.pk).filter(active_until__isnull=True).update(active_until=now+timedelta(minutes=20))
        if not claimed:return None
        return OneCDiagnosticRun.objects.create(connection=connection,created_by=actor,server=connection.server,database=connection.database,trust_certificate=connection.trust_certificate,report={'metadata_only':True} if metadata_only else {})


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
            command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(Path(settings.BASE_DIR)/'ops/diagnose-1c.ps1'),'-Root',str(root),'-RunHere','-PythonPath',__import__('sys').executable,'-Server',run.server,'-Database',run.database,'-RunId',run.pk.hex]
            metadata_only=bool(run.report.get('metadata_only'))
            if metadata_only:command.append('-MetadataOnly')
            if run.trust_certificate:command.append('-TrustServerCertificate')
            subprocess.run(command,timeout=900,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False,creationflags=subprocess.CREATE_NO_WINDOW)
            directory=root/'.runtime/1c-diagnostics'/run.pk.hex
            status=json.loads((directory/'status.json').read_text(encoding='utf-8-sig'))
            if not status.get('ok'):raise RuntimeError(status.get('error','Ошибка диагностики.'))
            run.report=json.loads((directory/'report.json').read_text(encoding='utf-8-sig'))
            if metadata_only:
                prior=run.connection.runs.filter(status='DONE',server=run.server,database=run.database).exclude(pk=run.pk).order_by('-created_at').first()
                if prior:
                    for key in ['candidates','reference_tables_checked','search_terms','sample_limit']:
                        if key in prior.report:run.report[key]=prior.report[key]
                    run.report['source_diagnostic_id']=str(prior.pk)
                run.report['metadata_only']=True
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
    mapping_error=None
    if request.method=='POST':
        if request.POST.get('action')=='save':
            form=ConnectionForm(request.POST,instance=connection)
            if form.is_valid():
                connection=form.save(commit=False)
                connection.save(update_fields=list(form.fields))
                messages.success(request,'Настройки 1С сохранены.')
                return redirect('accounts:one_c')
        elif request.POST.get('action')=='mapping':
            from .one_c_catalog import parse_mapping
            upload=request.FILES.get('mapping')
            try:
                if not upload or upload.size>32*1024*1024:raise ValidationError('Выберите JSON-файл до 32 МБ.')
                report_run=connection.runs.filter(status='DONE',server=connection.server,database=connection.database).order_by('-created_at').first()
                if not report_run:raise ValidationError('Сначала выполните диагностику текущего подключения.')
                labels=parse_mapping(json.loads(upload.read().decode('utf-8-sig')),report_run.report)
                existing=connection.metadata_labels if (connection.metadata_server,connection.metadata_database)==(connection.server,connection.database) else {}
                connection.metadata_labels={**existing,**labels};connection.metadata_server=connection.server;connection.metadata_database=connection.database
                connection.save(update_fields=['metadata_labels','metadata_server','metadata_database'])
                from apps.audit.models import AuditLog
                AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='accounts.onecconnection',entity_id=str(connection.pk),new_value={'mapped_tables':len(connection.metadata_labels),'company_id':request.user.company_id},comment='Импорт соответствий метаданных 1С')
                messages.success(request,f'Загружено соответствий таблиц: {len(labels)}.')
                return redirect('accounts:one_c')
            except (ValueError,UnicodeError,ValidationError) as exc:mapping_error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Не удалось прочитать JSON-файл.'
        elif request.POST.get('action') in {'diagnose','metadata_retry'}:
            run=enqueue(connection,request.user,metadata_only=request.POST.get('action')=='metadata_retry')
            messages.success(request,'Диагностика поставлена в очередь. Фоновый обработчик начнёт её в течение минуты.' if run else 'Диагностика уже ожидает запуска или выполняется.')
            return redirect('accounts:one_c')
    runs=list(connection.runs.defer('report').order_by('-created_at')[:20])
    latest=connection.runs.filter(status='DONE',server=connection.server,database=connection.database).order_by('-created_at').first()
    manual_labels=connection.metadata_labels if (connection.metadata_server,connection.metadata_database)==(connection.server,connection.database) else {}
    from .one_c_catalog import catalogue,TYPES
    from django.core.paginator import Paginator
    query=request.GET.get('q','').strip()[:200]
    kind=request.GET.get('type','')
    if kind not in dict(TYPES):kind=''
    report=latest.report if latest else {}
    labels={**report.get('metadata_labels',{}),**manual_labels}
    rows,type_counts,total=catalogue(report,labels,query,kind)
    page=Paginator(rows,75).get_page(request.GET.get('page'))
    groups=[]
    for row in page:
        row['type_label']=dict(TYPES)[row['type']]
        if not groups or groups[-1]['type']!=row['type']:groups.append({'type':row['type'],'label':row['type_label'],'rows':[]})
        groups[-1]['rows'].append(row)
    for candidate in report.get('candidates',[]):candidate['name']=labels.get(str(candidate.get('schema','dbo'))+'.'+str(candidate.get('table','')),{}).get('name','')
    params=request.GET.copy();params.pop('page',None)
    return render(request,'accounts/one_c.html',{'form':form,'connection':connection,'runs':runs,'latest':latest,'pending':any(r.status in {'QUEUED','RUNNING'} for r in runs),'catalogue_groups':groups,'catalogue_page':page,'catalogue_total':total,'catalogue_query':query,'catalogue_type':kind,'type_counts':type_counts,'catalogue_params':params.urlencode(),'mapping_error':mapping_error,'mapped_count':len(labels)})
