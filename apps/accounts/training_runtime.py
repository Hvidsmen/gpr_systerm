"""One supervised OS process and database per group; no multi-database ORM router."""
import json
import hashlib
import logging
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from urllib.request import build_opener, ProxyHandler
from django.conf import settings
from django.db import close_old_connections, connections
from django.utils import timezone
from ops.windows_runtime import atomic_json, read_json, singleton, rotate_output
from .training_models import TrainingCopy, TrainingResetRun
from . import training

log = logging.getLogger(__name__)


def code_schema():
    from django.db.migrations.recorder import MigrationRecorder
    migrations=sorted(MigrationRecorder(connections['default']).applied_migrations())
    return hashlib.sha256(json.dumps([str(Path(settings.BASE_DIR).resolve()),migrations]).encode()).hexdigest()


def child_environment(control, generation):
    # Do not inherit .env/LDAP/SMTP/1C/update credentials or production session keys.
    env = {key:value for key,value in os.environ.items() if key.upper() in {
        'PATH','SYSTEMROOT','WINDIR','COMSPEC','PATHEXT','TEMP','TMP','LANG','LC_ALL','PYTHONPATH'}}
    env.update(DJANGO_SETTINGS_MODULE='config.training_settings', GPR_CONFIG_DIR=str(generation),
        DJANGO_DB_PATH=str(generation/'db.sqlite3'), DJANGO_SECRET_KEY=(generation/'secret.key').read_text().strip(),
        GPR_TRAINING_CONTROL=str(control), GPR_TRAINING_GENERATION=generation.name,
        GPR_TRAINING_PARENT_PIPE='1',
        GPR_TRAINING_STATIC_ROOT=str(settings.STATIC_ROOT), DJANGO_DEBUG='0',
        DJANGO_ALLOWED_HOSTS=','.join(h for h in set(settings.ALLOWED_HOSTS)|{'localhost','127.0.0.1'} if h != '*'),
        DJANGO_SECURE_SSL_REDIRECT='0', DJANGO_COOKIE_SECURE=str(int(settings.SESSION_COOKIE_SECURE)),
        PYTHONUNBUFFERED='1')
    return env


def snapshot(source, target):
    source = Path(source).resolve(); target = Path(target).resolve()
    if not source.is_file() or source == target or target.exists() or source.is_relative_to(training.root()):
        raise RuntimeError('Нужна отдельная существующая основная база SQLite.')
    started = time.monotonic()
    def progress(*_):
        if time.monotonic()-started > 600:
            raise RuntimeError('Превышено время создания учебного снимка.')
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=10) as live:
        with sqlite3.connect(target) as copy:
            live.backup(copy,pages=256,progress=progress,sleep=.05)
            if copy.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('Учебный снимок не прошёл проверку SQLite.')


def copy_files(source, target):
    source = Path(source).resolve(); target = Path(target).resolve()
    if not source.exists():
        target.mkdir(parents=True,exist_ok=True); return
    if target.is_relative_to(source) or source.is_relative_to(training.root()):
        raise RuntimeError('Нельзя совмещать рабочие и учебные папки файлов.')
    for item in source.rglob('*'):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)() or not item.resolve().is_relative_to(source):
            raise RuntimeError('Ссылки на другие папки не копируются в учебную среду.')
    shutil.copytree(source,target)


class Manager:
    def __init__(self, stop=None):
        self.stop = stop or threading.Event()
        self.workers = {}
        self.retry_after = {}

    def start(self, group, current):
        control = training.folder(group)
        generation = control / current['generation']
        if generation.parent != control or not (generation/'db.sqlite3').is_file():
            raise RuntimeError('Не найдена подготовленная учебная база.')
        (generation/'stop').unlink(missing_ok=True)
        with socket.socket() as check:
            check.settimeout(.5)
            # A raw bind probe rejects a just-closed server's TIME_WAIT sockets.
            # Detect listeners instead; Waitress performs the real bind safely.
            if check.connect_ex(('127.0.0.1',group.port)) == 0:
                raise RuntimeError('Порт учебной копии уже занят другим процессом.')
        rotate_output(control/'http.log')
        with (control/'http.log').open('ab') as output:
            process = subprocess.Popen([sys.executable,str(Path(settings.BASE_DIR)/'manage.py'),
                'serve_training_copy','--port',str(group.port)], cwd=settings.BASE_DIR,
                env=child_environment(control,generation),stdin=subprocess.PIPE,stdout=output,stderr=subprocess.STDOUT)
        self.workers[group.pk] = (process,generation)
        opener = build_opener(ProxyHandler({}))
        for _ in range(100):
            if process.poll() is not None or self.stop.is_set():
                break
            try:
                with opener.open(f'http://127.0.0.1:{group.port}/accounts/training/health/',timeout=1) as response:
                    if json.load(response).get('generation') == generation.name:
                        return
            except Exception:
                self.stop.wait(.2)
        self.stop_worker(group.pk)
        raise RuntimeError('Учебный HTTP-процесс не прошёл проверку запуска.')

    def stop_worker(self, pk):
        worker = self.workers.pop(pk,None)
        if not worker:
            return
        process,generation = worker
        (generation/'stop').touch()
        try:
            process.wait(timeout=100)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
        finally:
            if process.stdin:process.stdin.close()

    def reset(self, group, now):
        control = training.folder(group)
        training.write_access(group)
        previous = read_json(control/'current.json',{})
        previous_key = (control/'ticket.key').read_text() if (control/'ticket.key').exists() else None
        generation = control / uuid.uuid4().hex
        generation.mkdir()
        (generation/'secret.key').write_text(secrets.token_urlsafe(64))
        (generation/'secret.key').chmod(0o600)
        run = TrainingResetRun.objects.create(company_id=group.company_id,copy=group,
            automatic=not(group.requested_at and (not group.applied_request_at or group.requested_at>group.applied_request_at)))
        TrainingCopy.objects.filter(pk=group.pk).update(state='RESETTING',error='')
        activated = False
        try:
            source = connections['default'].settings_dict
            if source['ENGINE'] != 'django.db.backends.sqlite3':
                raise RuntimeError('Автоматические учебные копии поддерживают SQLite.')
            snapshot(source['NAME'],generation/'db.sqlite3')
            copy_files(settings.MEDIA_ROOT,generation/'media')
            copy_files(settings.ACTUATION_FILES_ROOT,generation/'private_uploads'/'actuation')
            result = subprocess.run([sys.executable,str(Path(settings.BASE_DIR)/'manage.py'),'prepare_training_copy'],
                cwd=settings.BASE_DIR,env=child_environment(control,generation),capture_output=True,text=True,timeout=180)
            rotate_output(control/'prepare.log')
            with (control/'prepare.log').open('a',encoding='utf-8') as output:
                output.write(result.stdout+result.stderr)
            if result.returncode:
                # The protected process log may contain details; the UI never shows credentials/SQL.
                raise RuntimeError('Не удалось подготовить учебный снимок. Проверьте версию схемы и свободное место.')
            if self.stop.is_set():
                raise RuntimeError('Обновление остановлено вместе с сервером.')
            # All preparation completes before users of the previous copy are disconnected.
            self.stop_worker(group.pk)
            (control/'ticket.key').write_text(secrets.token_urlsafe(64)); (control/'ticket.key').chmod(0o600)
            current = {'generation':generation.name,'created_at':now.isoformat(),'return_url':settings.PORTAL_PUBLIC_URL,
                'code_schema':code_schema()}
            atomic_json(control/'current.json',current); activated = True
            self.start(group,current)
            TrainingCopy.objects.filter(pk=group.pk).update(last_reset_at=now,applied_request_at=group.requested_at,state='READY',error='')
            run.status='DONE';run.message='База и файлы группы заменены снимком основного портала.'
            # Retain the previous generation until the next successful refresh for recovery.
            keep={generation.name,previous.get('generation')}
            for item in control.iterdir():
                if item.is_dir() and len(item.name)==32 and all(c in '0123456789abcdef' for c in item.name) and item.name not in keep:
                    try:shutil.rmtree(item)
                    except OSError:log.warning('Old training generation could not be removed: %s',item.name)
        except Exception:
            log.exception('Training refresh failed for %s',group.pk)
            if activated:
                self.stop_worker(group.pk)
                if previous:
                    atomic_json(control/'current.json',previous)
                    if previous_key:
                        (control/'ticket.key').write_text(previous_key)
                    if not self.stop.is_set():
                        try:self.start(group,previous)
                        except Exception:log.exception('Previous training copy could not restart')
                else:
                    (control/'current.json').unlink(missing_ok=True)
            if generation.exists():
                shutil.rmtree(generation,ignore_errors=True)
            message='Учебную копию обновить не удалось. Основная база не изменена; подробности — в журнале сервера.'
            live=self.workers.get(group.pk)
            TrainingCopy.objects.filter(pk=group.pk).update(state='READY' if live and live[0].poll() is None else 'ERROR',error=message)
            run.status='ERROR';run.message=message
            self.retry_after[group.pk]=time.monotonic()+300
        finally:
            run.finished_at=timezone.now();run.save(update_fields=['finished_at','status','message'])

    def tick(self, now=None):
        now = now or timezone.now()
        close_old_connections()
        groups = list(TrainingCopy.objects.all().select_related('company'))
        signature=code_schema() if groups else None
        active = {g.pk for g in groups if g.enabled}
        for pk in set(self.workers)-active:
            self.stop_worker(pk)
        for group in groups:
            if self.stop.is_set():break
            training.write_access(group); training.sync_tasks(group)
            if not group.enabled:
                TrainingCopy.objects.filter(pk=group.pk).update(state='STOPPED');continue
            if not training.member_ids(group):
                self.stop_worker(group.pk)
                TrainingCopy.objects.filter(pk=group.pk).update(state='STOPPED',error='Нет активных участников.');continue
            if self.retry_after.get(group.pk,0)>time.monotonic():continue
            requested=group.requested_at and (not group.applied_request_at or group.requested_at>group.applied_request_at)
            current=read_json(training.folder(group)/'current.json',{})
            if requested or current.get('code_schema')!=signature or not group.last_reset_at or group.last_reset_at<training.reset_slot(now):
                self.reset(group,now);continue
            worker = self.workers.get(group.pk)
            if worker and worker[0].poll() is None:
                continue
            if worker:self.workers.pop(group.pk,None)
            try:
                if not current:
                    self.reset(group,now)
                else:
                    self.start(group,current)
                    TrainingCopy.objects.filter(pk=group.pk).update(state='READY',error='')
            except Exception:
                log.exception('Training process could not start for %s',group.pk)
                TrainingCopy.objects.filter(pk=group.pk).update(state='ERROR',error='Не удалось запустить копию. Проверьте свободный порт и журнал сервера.')
                self.retry_after[group.pk]=time.monotonic()+300

    def close(self):
        for _,generation in self.workers.values():
            (generation/'stop').touch()
        for pk in list(self.workers):self.stop_worker(pk)


def supervise(stop=None):
    if getattr(settings,'TRAINING_MODE',False):
        raise RuntimeError('Учебный процесс не может создавать другие копии.')
    training.root().mkdir(parents=True,exist_ok=True)
    manager=Manager(stop)
    with singleton(training.root()/'supervisor.lock'):
        try:
            while not manager.stop.is_set():
                try:manager.tick()
                except Exception:log.exception('Training supervisor tick failed')
                manager.stop.wait(15)
        except KeyboardInterrupt:
            manager.stop.set()
        finally:
            manager.close();close_old_connections()
