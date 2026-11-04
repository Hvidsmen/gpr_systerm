import json,os,shutil,subprocess
from pathlib import Path
from datetime import timedelta
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('PERSONNEL_INITIAL_BROWSER_TESTS')=='1','Optional first-assignment state browser scenario')
class AssignmentInitialBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_first_assignment_mv_and_mo_with_initial_dates(self):
        from .test_housing import HousingTests
        from apps.resources.models import Brigade,Employee
        from apps.planning.models import GlobalPlanVersion
        from .models import StaffingSlot,CampStay,HousingEvent,Person
        HousingTests.setUpTestData.__func__(type(self))
        from django.utils import timezone
        self.day=timezone.localdate()
        role=Brigade.objects.create(company=self.company,name='Начальник участка')
        resting=Employee.objects.create(company=self.company,last_name='Сидоров',first_name='Сидор',position=self.position,employee_number='INITIAL-MO')
        GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,start_date=self.day.replace(day=1),end_date=self.day+timedelta(days=100),version_number=1,status='APPROVED',snapshot={'works':[],'resources':{'labor':[{'date':str(self.day),'brigade_id':role.pk,'planned_workers':'2','label':role.name}]}})
        self.client.force_login(self.hr)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day}',
                'mv_name':self.second_employee.display_name,'mo_name':resting.display_name,'existing_name':self.employee.display_name,'role':role.name,'arrival':str(self.day-timedelta(days=20)),'departure':str(self.day-timedelta(days=10))}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/assignment_initial.js')],capture_output=True,text=True,env=dict(os.environ,PERSONNEL_INITIAL_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(StaffingSlot.objects.filter(planning_position=role,initial_movement__isnull=False).count(),2)
        self.assertEqual(CampStay.objects.filter(person__employee=self.second_employee).count(),1)
        self.assertFalse(CampStay.objects.filter(person__employee=resting).exists())
        self.assertEqual(HousingEvent.objects.filter(person__employee__in=[self.second_employee,resting]).count(),2)
        print(result.stdout.strip())
