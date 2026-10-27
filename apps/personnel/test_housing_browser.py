import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.test import Client
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('HOUSING_BROWSER_TESTS')=='1','Optional housing Chromium scenario')
class HousingBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_camp_links_bulk_movements_calendar_and_production_pool(self):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.hr);hr_cookie=self.client.cookies['sessionid'].value
        production=Client();production.force_login(self.dispatcher)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'hr_cookie':hr_cookie,'production_cookie':production.cookies['sessionid'].value,
            'housing':reverse('personnel:housing'),'camps':reverse('personnel:camps'),'camp_create':reverse('personnel:camp_create'),
            'production':self.url,'camp':self.a.pk,'target':self.b.pk,'day':str(self.day),'employee':self.employee.pk,'second_employee':self.second_employee.pk,
            'person':self.person.pk,'second_person':self.second_person.pk,'objects':[self.obj.pk,self.obj2.pk,self.obj3.pk]}
        script=Path(__file__).resolve().parents[2]/'tests/browser/housing.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,HOUSING_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .models import CampStay,HousingEvent,Camp
        self.assertEqual(self.second_person.stays.count(),2)
        self.assertEqual(HousingEvent.objects.filter(person=self.second_person).count(),3)
        self.assertEqual(Camp.objects.get(name='Новый физический ВЖГ').objects_served.count(),2)
        print(result.stdout.strip())
