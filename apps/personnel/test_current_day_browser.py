import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.utils import timezone


@skipUnless(os.environ.get('PERSONNEL_TODAY_BROWSER_TESTS')=='1','Optional current-day personnel UI')
class CurrentPersonnelDayBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_current_day_compact_filters_calendar_and_movement_dates(self):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(type(self));self.client.force_login(self.hr)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date=2000-01-01',
                'today':str(timezone.localdate()),'yesterday':str(self.day),'name':self.employee.display_name,'person':self.person.pk}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/personnel_today.js')],capture_output=True,text=True,env=dict(os.environ,PERSONNEL_TODAY_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
