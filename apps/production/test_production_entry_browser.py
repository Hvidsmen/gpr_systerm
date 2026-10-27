import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('PRODUCTION_ENTRY_BROWSER_TESTS')=='1','Optional production fact Chromium scenario')
class ProductionEntryBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_parallel_claims_four_tabs_report_and_graph(self):
        from .test_production_entry import ProductionEntryTests
        ProductionEntryTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.users['DISPATCHER']);cookie=self.client.cookies['sessionid'].value
        from django.test import Client
        other_client=Client();other_client.force_login(self.second);second_cookie=other_client.cookies['sessionid'].value
        config={'base':self.live_server_url,'cookie':cookie,'second_cookie':second_cookie,'chromium':shutil.which('chromium'),
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','employee':self.employee.pk,'work':self.work.pk,'second_work':self.second_work.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_entry.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,PRODUCTION_ENTRY_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .dispatch_models import EmployeeActivity
        row=EmployeeActivity.objects.get(employee=self.employee,date=self.day)
        self.assertEqual(row.work_id,self.work.pk);self.assertTrue(row.assignment_reviewed)
        from .dispatch_models import ProductionEntryDay
        self.assertIsNotNone(ProductionEntryDay.objects.get(dispatcher=self.users['DISPATCHER']).confirmed_at)
        print(result.stdout.strip())
