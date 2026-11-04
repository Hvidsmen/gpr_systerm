import json,os,shutil,subprocess
from pathlib import Path
from datetime import timedelta
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .test_housing import HousingTests


@skipUnless(os.environ.get('PERSONNEL_ASSIGNMENT_BROWSER_TESTS')=='1','Optional real personnel assignment UI')
class PlanAssignmentBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_assign_from_empty_plan_header(self):
        from apps.resources.models import Brigade
        from apps.planning.models import GlobalPlanVersion
        from .models import StaffingSlot,CampStay,HousingEvent
        HousingTests.setUpTestData.__func__(type(self))
        role=Brigade.objects.create(company=self.company,name='Начальник участка')
        GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,start_date=self.day.replace(day=1),end_date=self.day+timedelta(days=100),version_number=1,status='APPROVED',snapshot={'works':[],'resources':{'labor':[{'date':str(self.day),'brigade_id':role.pk,'planned_workers':'1','label':role.name}]}})
        self.client.force_login(self.hr)
        before=CampStay.objects.count(),HousingEvent.objects.count()
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day}',
                'employee':self.second_employee.pk,'name':self.second_employee.display_name,'role':role.name,'day':str(self.day)}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/personnel_assignment.js')],capture_output=True,text=True,env=dict(os.environ,PERSONNEL_ASSIGNMENT_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(StaffingSlot.objects.filter(planning_position=role,person__employee=self.second_employee).count(),1)
        self.assertEqual((CampStay.objects.count(),HousingEvent.objects.count()),before)
        self.second_employee.refresh_from_db();self.assertEqual(self.second_employee.position_id,self.position.pk)
        print(result.stdout.strip())
