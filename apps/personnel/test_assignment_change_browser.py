import json,os,shutil,subprocess
from pathlib import Path
from datetime import timedelta
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('ASSIGNMENT_CHANGE_BROWSER_TESTS')=='1','Optional assignment change browser flow')
class AssignmentChangeBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_change_assignment_keeps_history_and_actual_presence(self):
        from .test_housing import HousingTests
        from apps.resources.models import Brigade
        from apps.planning.models import GlobalPlanVersion
        from .models import StaffingSlot,CampStay,HousingEvent
        HousingTests.setUpTestData.__func__(type(self))
        from django.utils import timezone
        self.day=timezone.localdate()
        old=Brigade.objects.create(company=self.company,name='Начальник участка');new=Brigade.objects.create(company=self.company,name='Производитель работ')
        self.client.force_login(self.hr)
        self.client.post(reverse('personnel:plan_assignment_create'),{'employee':self.second_employee.pk,'planning_position':old.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day-timedelta(days=5)),'pattern':self.pattern.pk,'initial-state':'MV','initial-arrival_date':str(self.day-timedelta(days=20))})
        source=StaffingSlot.objects.get(planning_position=old)
        GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,start_date=self.day.replace(day=1),end_date=self.day+timedelta(days=100),version_number=1,status='APPROVED',snapshot={'works':[],'resources':{'labor':[{'date':str(self.day),'brigade_id':r.pk,'planned_workers':'1','label':r.name} for r in (old,new)]}})
        before=CampStay.objects.count(),HousingEvent.objects.count()
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day}',
                'past':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day-timedelta(days=1)}','name':self.second_employee.display_name,'old_role':old.name,'new_role':new.name,'day':str(self.day),'history':reverse('personnel:housing_history',args=[source.person_id])}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/assignment_change.js')],capture_output=True,text=True,env=dict(os.environ,ASSIGNMENT_CHANGE_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        source.refresh_from_db();self.assertEqual(source.replaced_on,self.day)
        self.assertEqual(StaffingSlot.objects.get(previous_assignment=source).planning_position,new)
        self.assertEqual((CampStay.objects.count(),HousingEvent.objects.count()),before)
        print(result.stdout.strip())
