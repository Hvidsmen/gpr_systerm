from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import subprocess
import sys
from unittest import TestCase,skipUnless
from unittest.mock import Mock,patch
from ops import staging_venv


class StagingVenvTests(TestCase):
    def test_real_offline_bootstrap_targets_only_new_environment(self):
        import pip
        original=pip.__version__
        with TemporaryDirectory() as folder:
            root=Path(folder).resolve();target=root/'new-venv';log=root/'prepare.log'
            # Inherited stdin and Python path are deliberately unusable; -I/DEVNULL isolate both.
            env={**os.environ,'PYTHONPATH':str(root/'invalid'),'PYTHONHOME':str(root/'invalid')}
            python=staging_venv.create_test_venv(target,env=env,log=log)
            result=subprocess.check_output([python,'-I','-c','import sys,json,pip;print(json.dumps([sys.prefix,sys.base_prefix,pip.__file__]))'],env=env,text=True)
            prefix,base,pip_file=json.loads(result)
            self.assertEqual(Path(prefix).resolve(),target);self.assertNotEqual(Path(base).resolve(),target)
            self.assertTrue(Path(pip_file).resolve().is_relative_to(target))
            contents=log.read_text();self.assertIn('DONE Create isolated environment without ensurepip',contents);self.assertIn('DONE Install bundled pip',contents);self.assertIn('DONE Verify TEST pip',contents)
            with self.assertRaises(ValueError):staging_venv.create_test_venv(target,env=env,log=log)
        self.assertEqual(pip.__version__,original)

    def test_bootstrap_commands_skip_ensurepip_use_local_wheel_and_keep_timeouts(self):
        with TemporaryDirectory() as folder,patch.object(staging_venv,'run_setup') as run:
            target=Path(folder)/'new';env={'TEMP':folder};log=Path(folder)/'prepare.log'
            staging_venv.create_test_venv(target,env=env,log=log)
            calls=run.call_args_list;self.assertEqual(len(calls),3)
            self.assertIn('--without-pip',calls[0].args[0]);self.assertIn('-I',calls[0].args[0])
            self.assertIn('--python',calls[1].args[0]);self.assertIn('--no-index',calls[1].args[0]);self.assertIn('--no-input',calls[1].args[0]);self.assertTrue(calls[1].args[0][-1].endswith('.whl'))
            self.assertEqual([c.kwargs['timeout'] for c in calls],[300,300,60])
            self.assertTrue(all(c.kwargs['env']==env for c in calls))

    def test_failed_environment_creation_cannot_attempt_pip(self):
        with TemporaryDirectory() as folder,patch.object(staging_venv,'run_setup',side_effect=RuntimeError('creation failed')) as run:
            with self.assertRaises(RuntimeError):staging_venv.create_test_venv(Path(folder)/'new',env={},log=Path(folder)/'prepare.log')
            self.assertEqual(run.call_count,1)

    def test_real_timeout_stops_owned_process_and_leaves_phase_in_private_log(self):
        with TemporaryDirectory() as folder:
            log=Path(folder)/'prepare.log'
            with self.assertRaisesRegex(RuntimeError,'exceeded'):
                staging_venv.run_setup([sys.executable,'-I','-c','import time;print("setup-started",flush=True);time.sleep(60)'],env=dict(os.environ),log=log,label='Synthetic stalled creation',timeout=.5)
            self.assertIn('TIMEOUT Synthetic stalled creation',log.read_text())

    def test_reported_timeout_uses_only_owned_pid_tree_and_closed_stdin(self):
        child=Mock(pid=321);child.wait.side_effect=subprocess.TimeoutExpired('venv',300)
        with TemporaryDirectory() as folder,patch.object(staging_venv.subprocess,'Popen',return_value=child) as start,patch.object(staging_venv,'stop_owned_setup') as stop:
            with self.assertRaises(RuntimeError):staging_venv.run_setup(['python','-m','venv'],env={},log=Path(folder)/'prepare.log',label='Create environment',timeout=300)
            stop.assert_called_once();self.assertIs(stop.call_args.args[0],child)
            self.assertEqual(start.call_args.kwargs['stdin'],subprocess.DEVNULL)

    def test_missing_bundled_wheel_fails_before_any_subprocess(self):
        with TemporaryDirectory() as folder,patch.object(staging_venv,'bundled_pip_wheel',side_effect=RuntimeError('missing wheel')),patch.object(staging_venv,'run_setup') as run:
            with self.assertRaises(RuntimeError):staging_venv.create_test_venv(Path(folder)/'new',env={},log=Path(folder)/'prepare.log')
            run.assert_not_called()

    @skipUnless(os.name=='nt','Windows owned descendant termination')
    def test_windows_timeout_stops_owned_descendant_too(self):
        import ctypes,re
        from ctypes import wintypes
        with TemporaryDirectory() as folder:
            log=Path(folder)/'prepare.log'
            script='import subprocess,sys,time; child=subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"]); print("DESCENDANT:"+str(child.pid),flush=True);time.sleep(60)'
            with self.assertRaises(RuntimeError):staging_venv.run_setup([sys.executable,'-I','-c',script],env=dict(os.environ),log=log,label='Owned nested setup',timeout=2)
            pid=int(re.search(r'DESCENDANT:(\d+)',log.read_text()).group(1))
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];kernel.WaitForSingleObject.restype=wintypes.DWORD
            kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            handle=kernel.OpenProcess(0x100000|0x1000,False,pid)
            if handle:
                try:self.assertEqual(kernel.WaitForSingleObject(handle,0),0)
                finally:kernel.CloseHandle(handle)
