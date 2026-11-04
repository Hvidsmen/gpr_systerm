import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.conf import settings
from django.test import override_settings
from . import test_staging_updates as fixtures
from ops import staging_updates
from ops.windows_runtime import atomic_json, singleton


STAGING_MIDDLEWARE=list(settings.MIDDLEWARE)
if 'apps.accounts.staging_access.StagingGuard' not in STAGING_MIDDLEWARE:
    STAGING_MIDDLEWARE.insert(STAGING_MIDDLEWARE.index('core.impersonation.ActAsMiddleware'),'apps.accounts.staging_access.StagingGuard')


@override_settings(MIDDLEWARE=STAGING_MIDDLEWARE)
@skipUnless(os.environ.get('STAGING_PROGRESS_BROWSER_TESTS')=='1','Optional staging progress UI and interrupted connection scenario')
class StagingProgressBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_visible_progress_restart_reconnect_failure_login_and_refresh(self):
        fixtures.StagingUpdateViewTests.setUpTestData.__func__(type(self))
        fixtures.StagingUpdateViewTests.setUp(self)
        fixtures.StagingUpdateViewTests.latest(self);fixtures.StagingUpdateViewTests.refresh_ready(self)
        stamp=datetime.now(timezone.utc)
        job={'id':'synthetic-update','created_at':(stamp-timedelta(seconds=90)).isoformat(),
             'phase_started_at':(stamp-timedelta(seconds=15)).isoformat(),'updated_at':stamp.isoformat()}
        states={}
        with singleton(self.root/'.runtime/staging-supervisor.lock'):
            for phase,_ in staging_updates.UPDATE_STEPS:
                atomic_json(self.root/'.runtime/staging-update.json',{**job,'state':phase})
                states[phase]=self.client.get(self.status_url).json()
            atomic_json(self.root/'.runtime/staging-update.json',{**job,'state':'error','failed_stage':'applying','finished_at':stamp.isoformat()})
            states['error']=self.client.get(self.status_url).json()
            self.config['revision']='b'*40;atomic_json(self.root/'staging.json',self.config)
            atomic_json(self.root/'.runtime/staging-update.json',{**job,'state':'done','finished_at':stamp.isoformat()})
            states['done']=self.client.get(self.status_url).json();done_html=self.client.get(self.url).content.decode()
            self.config['revision']='a'*40;atomic_json(self.root/'staging.json',self.config)
            for phase in ['copying','starting','done']:
                atomic_json(self.root/'.runtime/staging-update.json',{**job,'kind':'refresh','state':phase,**({'finished_at':stamp.isoformat()} if phase=='done' else {})})
                states['refresh_'+phase]=self.client.get(self.status_url).json()
                if phase=='copying':refresh_html=self.client.get(self.url).content.decode()
            atomic_json(self.root/'.runtime/staging-update.json',{**job,'state':'queued'})
            config={'base':self.live_server_url,'path':self.url,'status_path':self.status_url,'cookie':self.client.cookies['sessionid'].value,
                    'chromium':shutil.which('chromium'),'states':states,'done_html':done_html,'refresh_html':refresh_html}
            result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/staging_progress.js')],capture_output=True,text=True,
                                  env=dict(os.environ,STAGING_PROGRESS_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())

