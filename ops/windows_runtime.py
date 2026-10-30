"""Windows task runner and staged updater. The bootstrap uses only stdlib + dotenv."""
import argparse
import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
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
from datetime import datetime, timezone
from urllib.request import Request, urlopen, build_opener, ProxyHandler
import uuid
import zipfile

REPOSITORY='Hvidsmen/gpr_systerm'
TASK='YDS Portal'


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with open(temp,'w',encoding='utf-8') as output:
        json.dump(value,output,ensure_ascii=False,indent=2);output.flush();os.fsync(output.fileno())
    os.replace(temp,path)


def read_json(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:return default


def python_at(directory):
    return str(Path(directory)/('Scripts/python.exe' if os.name=='nt' else 'bin/python'))


def environment(root,config):
    from dotenv import dotenv_values
    env={**os.environ,**{k:v for k,v in dotenv_values(root/'.env').items() if v is not None}}
    env.update(GPR_CONFIG_DIR=str(root),DJANGO_DB_PATH=config['database'],DJANGO_MEDIA_ROOT=config['media'],
               DJANGO_DEBUG='0',DJANGO_SECURE_SSL_REDIRECT='0',DJANGO_COOKIE_SECURE='0',
               DJANGO_SECRET_KEY=(root/'.runtime/secret.key').read_text().strip(),PYTHONUNBUFFERED='1')
    hosts=env.get('DJANGO_ALLOWED_HOSTS','').split(',')+['127.0.0.1','localhost',socket.gethostname()]+config.get('hosts',[])
    env['DJANGO_ALLOWED_HOSTS']=','.join(sorted({h.strip() for h in hosts if h.strip() and h.strip()!='*'}))
    return env


def log_setup(runtime, filename="service.log"):
    runtime.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(runtime/filename,maxBytes=5_000_000,backupCount=5,encoding='utf-8')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',handlers=[handler])


@contextlib.contextmanager
def singleton(path):
    file=open(path,'a+b')
    try:
        if file.seek(0,os.SEEK_END)==0:file.write(b'0');file.flush()
        file.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:file.close()


def rotate_output(path):
    path=Path(path)
    if not path.exists() or path.stat().st_size<10_000_000:return
    path.with_name(path.name+'.3').unlink(missing_ok=True)
    for index in [2,1]:
        previous=path.with_name(path.name+'.'+str(index))
        if previous.exists():os.replace(previous,path.with_name(path.name+'.'+str(index+1)))
    os.replace(path,path.with_name(path.name+'.1'))


def run(args,cwd,env):
    logging.info('Running %s',Path(args[0]).name+' '+' '.join(args[1:3]))
    # Dependency / management command diagnostics go to a protected log, never the console.
    rotate_output(Path(env['GPR_CONFIG_DIR'])/'.runtime/commands.log')
    with open(Path(env['GPR_CONFIG_DIR'])/'.runtime/commands.log','ab') as output:
        subprocess.run(args,cwd=str(cwd),env=env,stdout=output,stderr=subprocess.STDOUT,check=True,timeout=1800)


def backup_database(config,runtime):
    source=Path(config['database'])
    if not source.is_file():raise RuntimeError('Configured database does not exist; refusing to create a new one.')
    destination=runtime/'backups'/datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    destination.mkdir(parents=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db,sqlite3.connect(destination/'database.sqlite3') as backup:
        db.backup(backup)
        if backup.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Database integrity check failed.')
    envfile=runtime.parent/'.env'
    if envfile.is_file():shutil.copy2(envfile,destination/'.env')
    bootstrap=runtime/'bootstrap.py'
    if bootstrap.is_file():shutil.copy2(bootstrap,destination/'bootstrap.py')
    return destination/'database.sqlite3'


def restore_database(config,backup):
    destination=Path(config['database'])
    # Called only with all managed app processes stopped, before public activation.
    for suffix in ['-wal','-shm']:
        Path(str(destination)+suffix).unlink(missing_ok=True)
    shutil.copy2(backup,destination)


class RecoveryRequired(RuntimeError):
    pass


def atomic_copy(source,target):
    target=Path(target);temp=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
    shutil.copy2(source,temp);os.replace(temp,target)


def recover_database_switch(root):
    runtime=root/'.runtime';journal=read_json(runtime/'database-switch.json')
    if not journal:return
    if not journal.get('committed'):
        restore_database(journal['target_config'],Path(journal['target_backup']))
        atomic_json(runtime/'installed.json',journal['previous_config'])
        env_backup=Path(journal['target_backup']).parent/'.env'
        if journal['environment_existed']:atomic_copy(env_backup,root/'.env')
        else:(root/'.env').unlink(missing_ok=True)
        logging.warning('Recovered interrupted database selection; previous configuration retained.')
    (runtime/'database-switch.json').unlink(missing_ok=True)


def switch_database(root,target):
    from dotenv import set_key
    runtime=root/'.runtime';config=read_json(runtime/'installed.json')
    if not config:raise RuntimeError('Background installation was not found.')
    target=target if target.is_absolute() else root/target
    target=target.resolve()
    if not target.is_file():raise RuntimeError('Selected database does not exist.')
    with sqlite3.connect(target.as_uri()+'?mode=ro',uri=True) as db:
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'accounts_user','projects_project','django_migrations'}.issubset(tables):raise RuntimeError('Selected file is not a portal database.')
        names=[row[0] for row in db.execute('SELECT name FROM projects_project ORDER BY id LIMIT 10')]
    print('Selected database: '+str(target));print('Projects: '+', '.join(names))
    if target==Path(config['database']).resolve() and not (runtime/'database-switch.json').exists():print('Already using this database.');return
    # The PowerShell wrapper disables triggers first and ensures no updater is active.
    if read_json(runtime/'status.json',{}).get('state')=='updating' or (runtime/'update.request').exists():raise RuntimeError('An update is active or queued; wait until it finishes.')
    with singleton(runtime/'update.lock'):
        (runtime/'supervisor.stop').touch()
        # On Windows the supervisor holds a byte-range lock for its entire lifetime.
        acquired=False
        for attempt in range(150):
            try:
                lock=singleton(runtime/'supervisor.lock');lock.__enter__();acquired=True;break
            except OSError:time.sleep(1)
        if not acquired:raise RuntimeError('Server did not stop. Database selection was not changed.')
        try:
            if read_json(runtime/'transaction.json'):raise RuntimeError('An interrupted update must be recovered before switching databases.')
            recover_database_switch(root)
            config=read_json(runtime/'installed.json')
            current=read_json(runtime/'current.json')
            if not current or not Path(current['python']).is_file():raise RuntimeError('The active runtime interpreter is not available.')
            target_config={**config,'database':str(target)}
            old_backup=backup_database(config,runtime)
            target_backup=backup_database(target_config,runtime)
            journal={'previous_config':config,'target_config':target_config,'target_backup':str(target_backup),'previous_backup':str(old_backup),'environment_existed':(root/'.env').is_file(),'committed':False}
            atomic_json(runtime/'database-switch.json',journal)
            try:
                run([current['python'],'manage.py','check'],current['code'],environment(root,target_config))
                run([current['python'],'manage.py','migrate','--noinput'],current['code'],environment(root,target_config))
                set_key(str(root/'.env'),'DJANGO_DB_PATH',target.as_posix(),quote_mode='always')
                atomic_json(runtime/'installed.json',target_config)
                atomic_json(runtime/'database-switch.json',{**journal,'committed':True})
            except Exception:
                recover_database_switch(root);raise
            (runtime/'database-switch.json').unlink(missing_ok=True)
            print('Database selected. Previous database and selected database backups: '+str(old_backup.parent)+' ; '+str(target_backup.parent))
        finally:lock.__exit__(None,None,None)


def create_clean_database(root,target,keep_objects):
    runtime=root/'.runtime'
    config=read_json(runtime/'installed.json')
    target=(target if target.is_absolute() else root/target).resolve()
    if not config or target.exists():raise RuntimeError('Choose a new database filename; existing databases are never replaced.')
    if read_json(runtime/'status.json',{}).get('state')=='updating' or (runtime/'update.request').exists():raise RuntimeError('Wait for the current update to finish.')
    with singleton(runtime/'update.lock'):
        (runtime/'supervisor.stop').touch()
        lock=None
        for attempt in range(150):
            try:
                candidate=singleton(runtime/'supervisor.lock');candidate.__enter__();lock=candidate;break
            except OSError:time.sleep(1)
        if lock is None:raise RuntimeError('Server did not stop; no new database was created.')
        try:
            if read_json(runtime/'transaction.json') or read_json(runtime/'database-switch.json'):raise RuntimeError('Recover the interrupted operation before creating a new database.')
            current=read_json(runtime/'current.json')
            if not current:raise RuntimeError('Active application version not found.')
            backup=backup_database(config,runtime)
            print('Original database preserved. Backup: '+str(backup),flush=True)
            run([current['python'],'manage.py','create_users_database','--source',str(backup),'--target',str(target),'--object-access','keep' if keep_objects else 'clear'],current['code'],environment(root,config))
        finally:lock.__exit__(None,None,None)
    switch_database(root,target)


def recover_update(root):
    recover_database_switch(root)
    runtime=root/'.runtime';journal=read_json(runtime/'transaction.json')
    if not journal:return
    current=read_json(runtime/'current.json',{})
    if current.get('sha')!=journal['candidate']['sha']:
        config=read_json(runtime/'installed.json')
        restore_database(config,Path(journal['backup']))
        prior=Path(journal['backup']).parent/'bootstrap.py'
        if prior.exists():atomic_copy(prior,runtime/'bootstrap.py')
        atomic_json(runtime/'current.json',journal['previous'])
        logging.warning('Recovered an interrupted pre-activation update.')
    (runtime/'transaction.json').unlink(missing_ok=True)


def select_database(root,values,override=None):
    explicit=str(override) if override is not None else values.get('DJANGO_DB_PATH')
    database=Path(explicit) if explicit else None
    if database is not None and not database.is_absolute():database=root/database
    candidates={p.resolve() for p in [root/'db.sqlite3',root/'data/db.sqlite3',root/'data/fresh.sqlite3'] if p.is_file()}
    if database is not None and database.is_file():candidates.add(database.resolve())
    if override is None and len(candidates)>1:
        raise RuntimeError('Multiple existing databases found. Pass -DatabasePath to the Windows installer with the existing working database path. No database was changed.')
    if database is None:
        if len(candidates)!=1:raise RuntimeError('Specify the existing database with -DatabasePath.')
        database=next(iter(candidates))
    if not database.is_file():raise RuntimeError('Existing database was not found.')
    return database.resolve()


def initialize(root,port,hosts,database_override=None):
    from dotenv import dotenv_values
    runtime=root/'.runtime';runtime.mkdir(exist_ok=True)
    if (runtime/'installed.json').exists():raise RuntimeError('Already installed. Use the existing tasks instead of overwriting runtime configuration.')
    values={**os.environ,**{k:v for k,v in dotenv_values(root/'.env').items() if v is not None}}
    database=select_database(root,values,database_override)
    with socket.socket() as check:check.bind(('0.0.0.0',port))
    media=Path(values.get('DJANGO_MEDIA_ROOT',root/'media'));media=media if media.is_absolute() else root/media
    config={'database':str(database.resolve()),'media':str(media.resolve()),'port':port,'hosts':hosts}
    key=values.get('DJANGO_SECRET_KEY','')
    if len(key)<50 or key.startswith('django-insecure-'):key=secrets.token_urlsafe(64)
    (runtime/'secret.key').write_text(key,encoding='utf-8')
    env=environment(root,config)
    code={'code':str(root),'python':sys.executable,'sha':'initial'}
    backup=backup_database(config,runtime)
    try:
        run([sys.executable,'manage.py','check'],root,env)
        run([sys.executable,'manage.py','migrate','--noinput'],root,env)
        run([sys.executable,'manage.py','collectstatic','--noinput'],root,env)
    except Exception:
        restore_database(config,backup);raise
    atomic_json(runtime/'current.json',code)
    from dotenv import set_key
    set_key(str(root/'.env'),'DJANGO_DB_PATH',database.as_posix(),quote_mode='always')
    atomic_json(runtime/'installed.json',config)
    logging.info('Initialized, database backup: %s',backup)


def serve(root,code,port,probe=False):
    sys.path.insert(0,str(code));os.chdir(code)
    config=read_json(root/'.runtime/installed.json')
    os.environ.update(environment(root,config));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
    from django.core.wsgi import get_wsgi_application
    from waitress import create_server
    from whitenoise import WhiteNoise
    from django.conf import settings
    application=WhiteNoise(get_wsgi_application(),root=str(settings.STATIC_ROOT),prefix=settings.STATIC_URL)
    server=create_server(application,listen=('127.0.0.1' if probe else '0.0.0.0')+':'+str(port),threads=8,
                         max_request_body_size=128*1024*1024,channel_timeout=120)
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    stop=root/'.runtime'/('probe.stop' if probe else 'server.stop')
    while thread.is_alive() and not stop.exists():time.sleep(.5)
    server.close();server.task_dispatcher.shutdown(timeout=90);thread.join(timeout=10)
    if not stop.exists():raise RuntimeError('HTTP server exited unexpectedly.')


def launch(root,current,probe=False):
    config=read_json(root/'.runtime/installed.json');port=config['port']
    if probe:
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    stop=root/'.runtime'/('probe.stop' if probe else 'server.stop');stop.unlink(missing_ok=True)
    args=[current['python'],str(Path(current['code'])/'ops/windows_runtime.py'),'serve','--root',str(root),'--code',current['code'],'--port',str(port)]
    if probe:args.append('--probe')
    rotate_output(root/'.runtime/http.log')
    with open(root/'.runtime/http.log','ab') as output:
        process=subprocess.Popen(args,cwd=current['code'],env=environment(root,config),stdout=output,stderr=subprocess.STDOUT)
    return process,port


def stop_process(root,process,probe=False):
    if process is None:return
    stop=root/'.runtime'/('probe.stop' if probe else 'server.stop');stop.touch()
    try:process.wait(timeout=115)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=15)
    stop.unlink(missing_ok=True)


def healthy(process,port):
    for _ in range(60):
        if process.poll() is not None:raise RuntimeError('Candidate server failed to start; see http.log.')
        try:
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:'+str(port)+'/accounts/login/',timeout=2) as response:
                if response.status==200:return
        except Exception:time.sleep(1)
    raise RuntimeError('Candidate did not pass its login page health check.')


def extract_archive(archive,destination):
    with zipfile.ZipFile(archive) as zipped:
        files=zipped.infolist()
        if sum(f.file_size for f in files)>500_000_000:raise RuntimeError('Update archive is too large.')
        roots={f.filename.split('/')[0] for f in files}
        if len(roots)!=1:raise RuntimeError('Invalid archive layout.')
        for member in files:
            parts=Path(member.filename).parts[1:]
            if not parts:continue
            if any(p in {'..','.runtime','.env','.git'} for p in parts) or Path(member.filename).is_absolute() or (member.external_attr>>16)&0o170000==0o120000:
                raise RuntimeError('Unsafe update archive member.')
            target=destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination.resolve()):raise RuntimeError('Unsafe archive path.')
            if member.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with zipped.open(member) as source,open(target,'wb') as out:shutil.copyfileobj(source,out)
    for required in ['manage.py','requirements.txt','ops/windows_runtime.py']:
        if not (destination/required).is_file():raise RuntimeError('Incomplete application archive.')


def update_stage(root,stage):
    path=root/'.runtime/status.json';status=read_json(path,{})
    if status.get('state')=='updating':
        atomic_json(path,{**status,'stage':stage,'updated_at':datetime.now(timezone.utc).isoformat()})


def stage_update(root,config,current):
    update_stage(root,'checking')
    env=environment(root,config);headers={'Accept':'application/vnd.github+json','User-Agent':'YDS-Portal-Updater'}
    token=env.get('GPR_UPDATE_TOKEN','').strip()
    if token:headers['Authorization']='Bearer '+token
    endpoint='https://api.github.com/repos/'+REPOSITORY
    with urlopen(Request(endpoint+'/commits/main',headers=headers),timeout=45) as response:sha=json.load(response)['sha']
    if len(sha)!=40 or any(c not in '0123456789abcdef' for c in sha):raise RuntimeError('Invalid commit identity.')
    if sha==current.get('sha'):return None
    destination=root/'.runtime/releases'/sha
    if destination.exists():shutil.rmtree(destination)
    destination.mkdir(parents=True)
    update_stage(root,'downloading')
    archive=root/'.runtime/update.zip'
    with urlopen(Request(endpoint+'/zipball/'+sha,headers=headers),timeout=120) as response,open(archive,'wb') as output:
        size=0
        while True:
            chunk=response.read(1024*1024)
            if not chunk:break
            size+=len(chunk)
            if size>100_000_000:raise RuntimeError('Update download exceeds the size limit.')
            output.write(chunk)
    extract_archive(archive,destination);archive.unlink(missing_ok=True)
    update_stage(root,'dependencies')
    venv=root/'.runtime/venvs'/sha
    run([sys.executable,'-m','venv',str(venv)],root,env)
    interpreter=python_at(venv)
    run([interpreter,'-m','pip','install','-r',str(destination/'requirements.txt')],destination,env)
    update_stage(root,'validation')
    run([interpreter,'manage.py','check'],destination,env)
    run([interpreter,'manage.py','collectstatic','--noinput'],destination,env)
    return {'sha':sha,'code':str(destination),'python':interpreter}


def update(root,process,current):
    runtime=root/'.runtime';config=read_json(runtime/'installed.json')
    candidate=stage_update(root,config,current)
    if not candidate:return process,current,'Already up to date.'
    update_stage(root,'stopping')
    stop_process(root,process)
    update_stage(root,'backup')
    backup=backup_database(config,runtime)
    atomic_json(runtime/'transaction.json',{'previous':current,'candidate':candidate,'backup':str(backup)})
    probe=None
    try:
        update_stage(root,'migrating')
        run([candidate['python'],'manage.py','migrate','--noinput'],candidate['code'],environment(root,config))
        update_stage(root,'candidate')
        probe,port=launch(root,candidate,probe=True);healthy(probe,port);stop_process(root,probe,probe=True);probe=None
        atomic_copy(Path(candidate['code'])/'ops/windows_runtime.py',runtime/'bootstrap.py')
        atomic_json(runtime/'current.json',candidate)
    except Exception:
        stop_process(root,probe,probe=True)
        try:recover_update(root)
        except Exception as exc:raise RecoveryRequired('Database recovery must finish before the server is started.') from exc
        old,_=launch(root,current)
        logging.exception('Update rejected; previous code and database restored.')
        return old,current,'Update failed. Previous version restored; see protected logs.'
    update_stage(root,'starting')
    new,_=launch(root,candidate)
    (runtime/'transaction.json').unlink(missing_ok=True)
    # Once the public listener is started, do not restore its database: users may write facts.
    return new,candidate,'Updated to '+candidate['sha'][:12]+'. Database backup: '+str(backup)


def windows_job():
    """Kill managed descendants if the supervisor is forcibly terminated."""
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',wintypes.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',wintypes.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',wintypes.DWORD),('SchedulingClass',wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ['ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount']]
    class Extended(ctypes.Structure):
        _fields_=[('BasicLimitInformation',Basic),('IoInfo',Counters),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];kernel.CreateJobObjectW.restype=wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE]
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    job=kernel.CreateJobObjectW(None,None)
    limits=Extended();limits.BasicLimitInformation.LimitFlags=0x2000
    if not job or not kernel.SetInformationJobObject(job,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(job,kernel.GetCurrentProcess()):raise ctypes.WinError(ctypes.get_last_error())
    # Intentionally keep this handle open for the supervisor lifetime.
    return job


def supervise(root):
    runtime=root/'.runtime'
    with singleton(runtime/'supervisor.lock'):
        job=windows_job()
        recover_update(root)
        (runtime/'supervisor.stop').unlink(missing_ok=True)
        current=read_json(runtime/'current.json');process,port=launch(root,current)
        atomic_json(runtime/'status.json',{'state':'running','sha':current['sha'],'pid':process.pid})
        integration_worker=None
        integration_tick=0
        invitation_worker=None
        invitation_tick=0
        try:
            while not (runtime/'supervisor.stop').exists():
                request=read_json(runtime/'update.request')
                if request:
                    (runtime/'update.request').unlink(missing_ok=True)
                    if not isinstance(request,dict) or not isinstance(request.get('id'),str) or len(request['id'])!=32 or any(c not in '0123456789abcdef' for c in request['id']):
                        logging.error('Invalid update request ignored.');continue
                    atomic_json(runtime/'status.json',{'state':'updating','request_id':request['id'],'sha':current['sha']})
                    if integration_worker is not None and integration_worker.poll() is None:
                        integration_worker.terminate();integration_worker.wait(timeout=15)
                    if invitation_worker is not None and invitation_worker.poll() is None:
                        invitation_worker.terminate();invitation_worker.wait(timeout=15)
                    try:
                        process,current,message=update(root,process,current)
                        update_stage(root,'verifying')
                        healthy(process,read_json(runtime/'installed.json')['port'])
                        result={'ok':not message.startswith('Update failed'),'message':message}
                    except RecoveryRequired:
                        atomic_json(runtime/'status.json',{'state':'recovery_required'})
                        raise
                    except Exception:
                        logging.exception('Update preparation or public start failed.')
                        current=read_json(runtime/'current.json',current)
                        result={'ok':False,'message':'Update did not finish. See current.json and the protected logs.'}
                    if process.poll() is not None:process,port=launch(root,current)
                    atomic_json(runtime/'results'/str(request['id']),result)
                    atomic_json(runtime/'status.json',{'state':'running','sha':current['sha'],'pid':process.pid,**result})
                if process.poll() is not None:
                    logging.error('HTTP worker exited; restarting in 5 seconds.');time.sleep(5);process,port=launch(root,current)
                if time.monotonic()>=integration_tick and (integration_worker is None or integration_worker.poll() is not None):
                    integration_tick=time.monotonic()+60
                    try:
                        rotate_output(runtime/'1c-worker.log')
                        with open(runtime/'1c-worker.log','ab') as output:
                            integration_worker=subprocess.Popen([current['python'],str(Path(current['code'])/'manage.py'),'process_1c'],cwd=current['code'],env=environment(root,read_json(runtime/'installed.json')),stdout=output,stderr=subprocess.STDOUT)
                    except OSError:logging.exception('Could not start 1C worker.')
                if time.monotonic()>=invitation_tick and (invitation_worker is None or invitation_worker.poll() is not None):
                    invitation_tick=time.monotonic()+30
                    try:
                        rotate_output(runtime/'invitations-worker.log')
                        with open(runtime/'invitations-worker.log','ab') as output:
                            invitation_worker=subprocess.Popen([current['python'],str(Path(current['code'])/'manage.py'),'process_invitations'],cwd=current['code'],env=environment(root,read_json(runtime/'installed.json')),stdout=output,stderr=subprocess.STDOUT)
                    except OSError:logging.exception('Could not start invitations worker.')
                time.sleep(1)
        finally:
            if invitation_worker is not None and invitation_worker.poll() is None:
                invitation_worker.terminate();invitation_worker.wait(timeout=15)
            if integration_worker is not None and integration_worker.poll() is None:
                integration_worker.terminate();integration_worker.wait(timeout=15)
            stop_process(root,process)
    (runtime/'supervisor.stop').unlink(missing_ok=True)


def request_update(root):
    runtime=root/'.runtime'
    with singleton(runtime/'update.lock'):
        if (runtime/'update.request').exists() or read_json(runtime/'status.json',{}).get('state')=='updating':raise RuntimeError('An update is already queued or running.')
        identifier=uuid.uuid4().hex
        atomic_json(runtime/'update.request',{'id':identifier})
        for _ in range(1800):
            result=read_json(runtime/'results'/identifier)
            if result:
                print(result['message'])
                if not result['ok']:raise RuntimeError('Update was not applied.')
                return
            time.sleep(1)
        raise RuntimeError('Update timed out. Check the startup task and service.log.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['init','supervise','serve','update','stop','status','switch-database','new-database'])
    parser.add_argument('--root',type=Path,required=True);parser.add_argument('--port',type=int,default=8001);parser.add_argument('--host',action='append',default=[]);parser.add_argument('--database',type=Path);parser.add_argument('--code',type=Path);parser.add_argument('--probe',action='store_true')
    parser.add_argument('--object-access',choices=['keep','clear'])
    args=parser.parse_args();root=args.root.resolve();os.chdir(root);log_setup(root/'.runtime',{'supervise':'service.log','serve':'worker.log','init':'installer.log','update':'updater.log'}.get(args.command,'control.log'))
    if args.command=='init':initialize(root,args.port,args.host,args.database)
    elif args.command=='supervise':supervise(root)
    elif args.command=='serve':serve(root,args.code.resolve(),args.port,args.probe)
    elif args.command=='update':request_update(root)
    elif args.command=='switch-database':
        if args.database is None:raise RuntimeError('Pass --database with an existing portal database path.')
        switch_database(root,args.database)
    elif args.command=='new-database':
        if args.database is None or args.object_access is None:raise RuntimeError('Pass --database and --object-access keep or clear.')
        create_clean_database(root,args.database,args.object_access=='keep')
    elif args.command=='stop':(root/'.runtime/supervisor.stop').touch()
    else:print(json.dumps(read_json(root/'.runtime/status.json',{}),ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except Exception:
        logging.exception('Runtime operation failed.');print('Operation failed. See .runtime/service.log and .runtime/commands.log.');sys.exit(1)
