import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_confirmed_staffing as fixtures


@skipUnless(os.environ.get('CONFIRMED_STAFFING_BROWSER_TESTS')=='1','Optional confirmed staffing Chromium workflow')
class ConfirmedStaffingBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_find_confirmed_positions_and_read_status_in_both_views_and_card(self):
        fixtures.ConfirmedStaffingTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.user)
        config={'base':self.live_server_url,'list':reverse('resources:brigade_list'),
            'manual':self.manual.pk,'retired':self.retired.pk,'current':self.current.pk,
            'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/confirmed_staffing.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,
            env=dict(os.environ,CONFIRMED_STAFFING_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
