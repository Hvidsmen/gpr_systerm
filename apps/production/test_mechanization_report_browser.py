import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('MECHANIZATION_REPORT_BROWSER_TESTS')=='1','Optional mechanization report Chromium scenario')
class MechanizationReportBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_role_limited_day_month_and_daily_details(self):
        from .test_mechanization_reports import MechanizationReportTests
        MechanizationReportTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.users['MECHANIZATION_DISPATCHER'])
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'path':reverse('production:equipment_report')+f'?construction_object={self.obj.pk}&date={self.start}',
            'category':self.category.name,'type':self.type.name,'person':self.employee.display_name}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/mechanization_report.js')],capture_output=True,text=True,
            env=dict(os.environ,MECHANIZATION_REPORT_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
