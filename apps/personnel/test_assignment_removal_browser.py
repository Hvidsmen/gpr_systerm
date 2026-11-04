import json
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('ASSIGNMENT_REMOVAL_BROWSER_TESTS')=='1','Optional assignment removal UI')
class AssignmentRemovalBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_add_another_employee_without_plan_then_remove_connection(self):
        from .test_housing import HousingTests
        from apps.resources.models import Brigade
        from .models import StaffingSlot, CampStay, HousingEvent
        HousingTests.setUpTestData.__func__(type(self))
        from django.utils import timezone
        self.day=timezone.localdate()
        role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.client.force_login(self.hr)
        response=self.client.post(reverse('personnel:plan_assignment_create'),{'employee':self.second_employee.pk,'planning_position':role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day-timedelta(days=5)),'pattern':self.pattern.pk,'initial-state':'MO','initial-departure_date':str(self.day-timedelta(days=6))})
        self.assertEqual(response.status_code,302)
        source=StaffingSlot.objects.get(planning_position=role)
        before=list(CampStay.objects.values()),list(HousingEvent.objects.values())
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day}',
                'name':self.second_employee.display_name,'another':self.employee.display_name,'role':role.name,'role_id':role.pk,'object_id':self.obj.pk,'camp_id':self.a.pk,'day':str(self.day),'remove':reverse('personnel:plan_assignment_remove',args=[source.pk]),'history':reverse('personnel:housing_history',args=[source.person_id])}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/assignment_removal.js')],capture_output=True,text=True,env=dict(os.environ,ASSIGNMENT_REMOVAL_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        source.refresh_from_db();self.assertIsNotNone(source.cancelled_at);self.assertEqual(source.cancelled_by,self.hr)
        self.assertEqual(StaffingSlot.objects.filter(planning_position=role,cancelled_at=None).count(),1)
        self.assertEqual((list(CampStay.objects.values()),list(HousingEvent.objects.values())),before)
        print(result.stdout.strip())
