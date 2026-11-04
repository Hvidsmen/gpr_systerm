"""Management startup must not wait on the TEST supervisor's parent pipe."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from ops import staging,staging_venv


class StagingManageTests(TestCase):
    def test_real_check_has_closed_stdin_correct_code_and_phase_log(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);code=root/'selected';code.mkdir()
            (code/'manage.py').write_text("import os,sys\nfrom pathlib import Path\nassert sys.stdin.buffer.read()==b''\nassert Path.cwd()==Path(__file__).parent\nassert Path(sys.path[0])==Path(__file__).parent\nprint('Check completed',flush=True)\n")
            staging.run_manage(root,['check'],code=code,python=sys.executable)
            log=(root/'.runtime/prepare.log').read_text()
            self.assertIn('START Django check',log);self.assertIn('DONE Django check',log);self.assertIn('Check completed',log)

    def test_secret_input_is_bounded_and_never_written_to_log_or_arguments(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);code=root/'selected';code.mkdir()
            secret='synthetic-'+('пароль'*2000)
            (code/'manage.py').write_text("import sys,hashlib\nprint(hashlib.sha256(sys.stdin.buffer.read()).hexdigest(),flush=True)\n")
            staging.run_manage(root,['prepare_staging_copy','--owner-password-hash'],code=code,python=sys.executable,password=secret)
            log=(root/'.runtime/prepare.log').read_text()
            self.assertIn(hashlib.sha256(secret.encode()).hexdigest(),log);self.assertNotIn(secret,log)

    def test_check_budget_is_two_minutes_and_other_commands_keep_existing_budget(self):
        with TemporaryDirectory() as folder,patch.object(staging_venv,'run_setup') as run:
            for command,budget in [('check',120),('migrate',1800),('collectstatic',1800)]:
                staging.run_manage(Path(folder),[command],python=sys.executable)
                self.assertEqual(run.call_args.kwargs['timeout'],budget)
                self.assertIsNone(run.call_args.kwargs['input_data'])
                self.assertIn('-I',run.call_args.args[0]);self.assertIn('-u',run.call_args.args[0])

    def test_stalled_startup_prints_stack_and_timeout_cleans_only_its_process(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);code=root/'selected';code.mkdir()
            (code/'manage.py').write_text("import time\nprint('Synthetic startup entered',flush=True)\ntime.sleep(60)\n")
            log=root/'prepare.log'
            with self.assertRaisesRegex(RuntimeError,'exceeded'):
                staging_venv.run_setup([sys.executable,'-I','-u',str(staging.CODE_ROOT/'ops/staging_manage.py'),'--code',str(code),'--trace-after','0.2','check'],cwd=code,env=dict(os.environ),log=log,label='Diagnostic stalled check',timeout=2.5)
            output=log.read_text();self.assertIn('Synthetic startup entered',output);self.assertIn('manage.py',output)
            self.assertIn('TIMEOUT Diagnostic stalled check',output)
