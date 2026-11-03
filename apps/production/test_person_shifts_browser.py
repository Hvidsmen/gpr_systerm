import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('PERSON_SHIFT_BROWSER_TESTS')=='1','Optional person shift browser scenario')
class PersonShiftBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_edit_saved_shift_and_collaborating_dispatcher_conflict(self):
        from .test_production_entry import ProductionEntryTests
        from .dispatch_models import EmployeeActivity, EmployeeWorkLink, EmployeeFactClaim
        ProductionEntryTests.setUpTestData.__func__(type(self))
        row=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,
            shift=1,entry_direction='works',work=self.work,reported_by=self.users['DISPATCHER'])
        link=EmployeeWorkLink.objects.create(company=self.company,activity=row,work=self.work)
        self.client.force_login(self.users['DISPATCHER']);cookie=self.client.cookies['sessionid'].value
        other=Client();other.force_login(self.second)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':cookie,'second_cookie':other.cookies['sessionid'].value,
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','employee':self.employee.pk,'activity':row.pk,'object':self.obj.pk,'day':str(self.day)}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/person_shifts.js')],capture_output=True,text=True,env=dict(os.environ,PERSON_SHIFT_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        row.refresh_from_db();self.assertEqual(row.shift,2);self.assertTrue(EmployeeWorkLink.objects.filter(pk=link.pk).exists())
        self.assertEqual(EmployeeActivity.objects.filter(employee=self.employee).count(),1)
        from .models import LaborFact
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
        print(result.stdout.strip())
