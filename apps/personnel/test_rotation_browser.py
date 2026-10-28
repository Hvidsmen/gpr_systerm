import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('ROTATION_BROWSER_TESTS')=='1','Optional rotation browser scenario')
class RotationBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_multiple_camps_rest_vacancies_calendar_and_export(self):
        from .test_housing import HousingTests
        from .models import StaffingSlot
        from apps.projects.models import ConstructionObject
        HousingTests.setUpTestData.__func__(type(self))
        StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,housing_camp=self.b,status='МО',pattern=self.pattern)
        StaffingSlot.objects.create(company=self.company,group=self.group,housing_camp=self.b,pattern=self.pattern)
        parent=ConstructionObject.objects.create(company=self.company,project=self.project,name='Общий КТП')
        for obj in [self.obj,self.obj3]:obj.parent=parent;obj.save()
        self.client.force_login(self.hr)
        config={'base':self.live_server_url,'url':reverse('personnel:housing'),'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'camp':self.a.pk,'second_camp':self.b.pk,'parent':parent.pk,'day':str(self.day),'person':self.person.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/rotation_view.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,ROTATION_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
