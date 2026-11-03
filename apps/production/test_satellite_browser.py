import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('SATELLITE_BROWSER_TESTS')=='1','Optional satellite network browser scenario')
class SatelliteBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_low_bandwidth_offline_queue_retry_and_reload(self):
        from .test_production_entry import ProductionEntryTests
        from .models import DailyFact
        from .dispatch_models import EmployeeActivity,ProductionLinkReceipt
        from apps.resources.models import Employee
        ProductionEntryTests.setUpTestData.__func__(type(self))
        for n in range(119):
            employee=Employee.objects.create(company=self.company,last_name=f'Тест спутник {n}',first_name='Сотрудник',position=self.employee.position)
            EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=employee,position=employee.position,entry_direction='works',assignment_reviewed=False,reported_by=self.users['DISPATCHER'])
        person=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,entry_direction='works',assignment_reviewed=False,reported_by=self.users['DISPATCHER'])
        one=DailyFact.objects.create(company=self.company,date=self.day,project_work=self.work,actual_quantity=5,reported_by=self.users['DISPATCHER'])
        two=DailyFact.objects.create(company=self.company,date=self.day,project_work=self.second_work,actual_quantity=3,reported_by=self.users['DISPATCHER'])
        self.client.force_login(self.users['DISPATCHER'])
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works&entry_step=3','activity':person.pk,'work':self.work.pk,'second_work':self.second_work.pk,'fact':one.pk,'second_fact':two.pk}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/satellite.js')],capture_output=True,text=True,env=dict(os.environ,SATELLITE_CONFIG=json.dumps(config)),timeout=240)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        person.refresh_from_db();self.assertEqual(person.work_links.count(),2)
        self.assertEqual(ProductionLinkReceipt.objects.count(),6)
        self.assertEqual(person.links_revision,6)
        print(result.stdout.strip())
