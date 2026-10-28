import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('PRODUCTION_POOL_BROWSER_TESTS')=='1','Optional unrestricted production employee Chromium scenario')
class ProductionEmployeePoolBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_all_active_groups_location_hints_fact_and_personnel_reconciliation(self):
        from .test_employee_pool import ProductionEmployeePoolTests
        ProductionEmployeePoolTests.setUpTestData.__func__(type(self))
        ProductionEmployeePoolTests.setUp(self)
        production=Client();production.force_login(self.operator);hr=Client();hr.force_login(self.users['PERSONNEL_DISPATCHER'])
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'production_cookie':production.cookies['sessionid'].value,'hr_cookie':hr.cookies['sessionid'].value,
            'production':self.url,'sync':reverse('personnel:lodging_sync'),'camp':self.home.pk,'other_camp':self.other.pk,'day':str(self.day),'object':self.obj.pk,
            'employee':self.employee.pk,'away':self.away.pk,'unknown':self.unknown.pk,'inactive':self.inactive.pk,'missing_role':self.missing_role.pk,'cook':self.cook.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_employee_pool.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,PRODUCTION_POOL_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .dispatch_models import EmployeeActivity
        from apps.personnel.models import CampStay,LodgingStay
        row=EmployeeActivity.objects.get(employee=self.unknown,date=self.day)
        self.assertEqual(row.entry_direction,'works');self.assertTrue(row.general)
        self.assertTrue(CampStay.objects.filter(person__employee=self.unknown,camp=self.other).exists())
        self.assertFalse(LodgingStay.objects.filter(employee=self.unknown).exists())
        print(result.stdout.strip())
