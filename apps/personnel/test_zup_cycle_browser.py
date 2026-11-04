import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('ZUP_CYCLE_BROWSER_TESTS')=='1','Optional ZUP cycle and personal override UI')
class ZupCycleBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_hr_cycle_and_personal_override_use_consistent_forecast(self):
        from .test_housing import HousingTests
        from apps.resources.models import Brigade
        from .models import StaffingSlot, CampStay, HousingEvent
        HousingTests.setUpTestData.__func__(type(self))
        self.second_employee.zup_card={'schedule':'Вахта 1.1М (60х30)'};self.second_employee.save()
        role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.client.force_login(self.hr)
        arrival=self.day-timedelta(days=9)
        response=self.client.post(reverse('personnel:plan_assignment_create'),{'employee':self.second_employee.pk,'planning_position':role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day),'initial-state':'MV','initial-arrival_date':str(arrival)})
        self.assertEqual(response.status_code,302)
        slot=StaffingSlot.objects.get(planning_position=role);self.assertIsNone(slot.pattern_id)
        before=list(CampStay.objects.values()),list(HousingEvent.objects.values())
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day}',
                'name':self.second_employee.display_name,'role':role.name,'day_number':self.day.day,'departure':(arrival+timedelta(days=60)).strftime('%d.%m.%Y'),'personal_departure':(arrival+timedelta(days=14)).strftime('%d.%m.%Y')}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/zup_cycle.js')],capture_output=True,text=True,env=dict(os.environ,ZUP_CYCLE_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        slot.refresh_from_db();self.assertEqual(slot.pattern.on_days,14);self.assertEqual(slot.pattern.off_days,7)
        self.assertEqual((list(CampStay.objects.values()),list(HousingEvent.objects.values())),before)
        print(result.stdout.strip())
