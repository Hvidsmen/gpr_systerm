"""Administrator UI for the existing Windows supervisor update protocol."""
import errno
import json
import os
from datetime import datetime,timezone as dt_timezone
from pathlib import Path
import re
import uuid
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect,render
from django.views.decorators.http import require_GET,require_http_methods
from core.permissions import require_roles
from ops.windows_runtime import atomic_json,singleton

STAGES={'checking':'Проверка новой версии','downloading':'Скачивание новой версии','dependencies':'Установка зависимостей','validation':'Проверка приложения','stopping':'Завершение текущих запросов','backup':'Резервная копия базы','migrating':'Обновление структуры базы','candidate':'Проверка новой версии','starting':'Запуск портала','verifying':'Проверка доступности портала'}


def is_windows():return os.name=='nt'

def read(path,default=None):
    try:
        value=json.loads(path.read_text(encoding='utf-8-sig'))
        return value if not isinstance(default,dict) or isinstance(value,dict) else default
    except (OSError,ValueError):return default


def supervisor_running(runtime):
    try:
        with open(runtime/'supervisor.lock','r+b') as handle:
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                    msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
                return False
            except OSError as exc:
                return exc.errno in {errno.EACCES,errno.EAGAIN,errno.EDEADLK}
    except OSError:return False


def state(company_id):
    runtime=Path(settings.CONFIG_DIR)/'.runtime'
    installed=read(runtime/'installed.json',{})
    current=read(runtime/'current.json',{})
    status=read(runtime/'status.json',{})
    metadata=read(runtime/'web-update.json',{})
    if not isinstance(metadata,dict) or not re.fullmatch('[0-9a-f]{32}',str(metadata.get('id',''))):metadata={}
    result=read(runtime/'results'/metadata['id']) if metadata else None
    if result is not None and not isinstance(result,dict):result=None
    queued=(runtime/'update.request').exists()
    active=status.get('state')=='updating'
    pending=False
    if metadata and result is None:
        try:pending=(datetime.now(dt_timezone.utc)-datetime.fromisoformat(metadata['created_at'])).total_seconds()<35*60
        except (ValueError,KeyError,TypeError):pass
    busy=queued or active or pending
    ready=is_windows() and bool(installed) and supervisor_running(runtime) and status.get('state')!='recovery_required'
    own=metadata.get('company_id')==company_id
    output={'ready':ready,'busy':busy,'version':str(current.get('sha',status.get('sha',''))),'state':'idle','message':'Можно запустить обновление.','stage':'','created_at':metadata.get('created_at') if own else None}
    if status.get('state')=='recovery_required':
        output.update(state='error',message='Обновление остановлено: требуется восстановление сервера. Проверьте журнал фонового портала.')
    elif active:
        output.update(state='updating',stage=STAGES.get(status.get('stage'),'Идёт обновление'),message='Обновление выполняется в фоне.')
    elif queued or pending:
        output.update(state='queued',stage='Ожидание фонового обработчика',message='Задание принято. Страница дождётся запуска и окончания обновления.')
    elif own and result is not None:
        if result.get('ok') is True:
            message='Установлена актуальная версия. Новых обновлений нет.' if str(result.get('message','')).startswith('Already up to date') else 'ГОТОВО. Обновление завершено, портал доступен.'
            output.update(state='done',message=message)
        else:
            message='Обновление не применено. Предыдущая версия восстановлена.' if str(result.get('message','')).startswith('Update failed') else 'Обновление не завершено. Проверьте журнал фонового портала.'
            output.update(state='error',message=message)
    elif own and metadata:
        output.update(state='error',message='Не удалось подтвердить завершение последнего обновления. Проверьте состояние фонового портала.')
    stages=list(STAGES)
    number=stages.index(status['stage'])+1 if active and status.get('stage') in stages else 0
    output.update(stage_number=number,stage_total=len(stages),progress=100 if output['state']=='done' else ((number-1)*100//len(stages) if number else 0),finished_at=None)
    if own and result is not None:
        try:output['finished_at']=datetime.fromtimestamp((runtime/'results'/metadata['id']).stat().st_mtime,dt_timezone.utc).isoformat()
        except OSError:pass
    if not ready and not busy and output['state']=='idle':output['message']='Обновление из браузера доступно при установленном и запущенном фоновом портале Windows.'
    return output


def enqueue(user):
    require_roles(user,{'ADMIN'})
    runtime=Path(settings.CONFIG_DIR)/'.runtime'
    if not is_windows() or not read(runtime/'installed.json'):raise ValueError('Фоновый портал Windows не установлен.')
    try:
        with singleton(runtime/'update.lock'):
            status=state(user.company_id)
            if status['busy']:raise ValueError('Обновление уже запущено. Дождитесь его окончания.')
            if not status['ready']:raise ValueError('Фоновый портал не работает или требует восстановления.')
            identifier=uuid.uuid4().hex
            metadata={'id':identifier,'user_id':user.pk,'company_id':user.company_id,'created_at':datetime.now(dt_timezone.utc).isoformat(),'from_sha':status['version']}
            from apps.audit.models import AuditLog
            with transaction.atomic():
                AuditLog.objects.create(user=user,action='UPDATE',entity_type='ops.PortalUpdate',entity_id=identifier,new_value={'from_sha':status['version'],'branch':'main'},comment='Запуск обновления портала из административной панели')
                atomic_json(runtime/'web-update.json',metadata)
                try:atomic_json(runtime/'update.request',{'id':identifier})
                except OSError:
                    (runtime/'web-update.json').unlink(missing_ok=True)
                    raise
            return identifier
    except OSError:raise ValueError('Не удалось поставить обновление в очередь: оно уже выполняется либо нет доступа к служебной папке.')


@login_required
@require_http_methods(['GET','POST'])
def updates(request):
    require_roles(request.user,{'ADMIN'})
    if request.method=='POST':
        try:
            enqueue(request.user)
            messages.success(request,'Обновление запущено в фоне. Можно закрыть страницу и вернуться позднее.')
        except ValueError as exc:messages.error(request,str(exc))
        return redirect('accounts:portal_updates')
    return render(request,'accounts/portal_updates.html',{'update':state(request.user.company_id)})


@login_required
@require_GET
def update_status(request):
    require_roles(request.user,{'ADMIN'})
    response=JsonResponse(state(request.user.company_id))
    response['Cache-Control']='no-store'
    return response
