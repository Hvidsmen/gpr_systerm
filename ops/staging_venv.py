"""Observable, non-interactive preparation of an owned TEST release environment."""
from datetime import datetime, timezone
import ensurepip
import os
from pathlib import Path
import signal
import subprocess
import sys
import sysconfig
import time
from ops.windows_runtime import python_at


def bundled_pip_wheel():
    name=f'pip-{ensurepip.version()}-py3-none-any.whl'
    folders=[Path(ensurepip.__file__).parent/'_bundled']
    if sysconfig.get_config_var('WHEEL_PKG_DIR'):folders.append(Path(sysconfig.get_config_var('WHEEL_PKG_DIR')))
    for folder in folders:
        if (folder/name).is_file():return folder/name
    raise RuntimeError('The Python installation has no bundled pip wheel. Restore ensurepip in the TEST Python installation.')


def stop_owned_setup(child,output):
    if child.poll() is not None:return
    if os.name=='nt':
        taskkill=Path(os.environ.get('SYSTEMROOT',r'C:\Windows'))/'System32/taskkill.exe'
        try:
            subprocess.run([str(taskkill),'/PID',str(child.pid),'/T','/F'],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,timeout=20,check=False)
        finally:
            if child.poll() is None:child.kill()
            child.wait(timeout=10)
    else:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    if os.name!='nt':
        if child.poll() is None:child.kill()
        child.wait(timeout=10)


def run_setup(args,*,env,log,label,timeout,cwd=None,input_data=None):
    log=Path(log);log.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    with log.open('ab',buffering=0) as output:
        def message(text):output.write((datetime.now(timezone.utc).isoformat()+' TEST setup: '+text+'\n').encode('utf-8'))
        message('START '+label)
        options={'creationflags':subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=='nt' else {'start_new_session':True}
        if cwd is not None:options['cwd']=str(cwd)
        child=subprocess.Popen(args,env=env,stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,**options)
        try:
            if input_data is None:code=child.wait(timeout=timeout)
            else:
                child.communicate(input=input_data,timeout=timeout)
                code=child.returncode
            if code:raise subprocess.CalledProcessError(code,args)
        except subprocess.TimeoutExpired as exc:
            message(f'TIMEOUT {label} after {timeout}s; stopping only the owned setup process tree')
            stop_owned_setup(child,output)
            raise RuntimeError(f'{label} exceeded {timeout}s; see .runtime/prepare.log.') from exc
        except BaseException:
            stop_owned_setup(child,output)
            message('FAILED '+label)
            raise
        finally:
            if input_data is not None and child.stdin and not child.stdin.closed:child.stdin.close()
        message(f'DONE {label} ({time.monotonic()-started:.1f}s)')


def create_test_venv(target,*,env,log):
    target=Path(target)
    if target.exists():raise ValueError('Refusing to overwrite an existing TEST environment.')
    wheel=bundled_pip_wheel()
    # pip --python starts a second interpreter; it must also ignore inherited Python paths.
    env={key:value for key,value in env.items() if not key.upper().startswith('PYTHON')}
    base=getattr(sys,'_base_executable',sys.executable)
    run_setup([base,'-I','-m','venv','--without-pip',str(target)],env=env,log=log,label='Create isolated environment without ensurepip',timeout=300)
    python=python_at(target)
    # pip's supported --python mode can seed an empty venv from the local Python wheel.
    # No ensurepip child process, package index, inherited stdin or user pip settings.
    run_setup([sys.executable,'-I','-m','pip','--isolated','--python',python,'install','--no-index','--no-deps','--no-cache-dir','--disable-pip-version-check','--no-input',str(wheel)],env=env,log=log,label='Install bundled pip into TEST environment',timeout=300)
    run_setup([python,'-I','-m','pip','--version'],env=env,log=log,label='Verify TEST pip',timeout=60)
    return python
