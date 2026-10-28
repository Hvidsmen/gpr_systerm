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
        from calendar import monthrange
        from datetime import timedelta
        from .housing_plan import add_month
        from apps.planning.models import DailySchedule,DailyScheduleEntry,GlobalPlanVersion
        from apps.resources.models import Brigade
        from apps.resources.position_catalog import brigade_for
        month=self.day.replace(day=1);brigade=brigade_for(self.position);future_role=Brigade.objects.create(company=self.company,name='Инженер ПТО')
        smg=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=month,status='APPLIED',revision=1,created_by=self.hr)
        for role,count in [(brigade,3),(future_role.pk,2)]:
            DailyScheduleEntry.objects.create(schedule=smg,key='labor:'+str(role),kind='labor',label='Монтажник' if role==brigade else 'Инженер ПТО',rows=[{'date':str(month+timedelta(days=n)),'brigade_id':role,'planned_count':str(count)} for n in range(monthrange(month.year,month.month)[1])])
        values=[]
        for offset in range(5):
            current=add_month(month,offset)
            values.extend({'date':str(current+timedelta(days=n)),'brigade_id':brigade,'planned_workers':'10','label':'Монтажник'} for n in range(monthrange(current.year,current.month)[1]))
        GlobalPlanVersion.objects.create(company=self.company,construction_object=parent,start_date=month,end_date=add_month(month,5)-timedelta(days=1),version_number=1,status='APPROVED',snapshot={'works':[],'resources':{'labor':values}})
        self.client.force_login(self.hr)
        config={'base':self.live_server_url,'url':reverse('personnel:housing'),'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'camp':self.a.pk,'second_camp':self.b.pk,'parent':parent.pk,'day':str(self.day),'person':self.person.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/rotation_view.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,ROTATION_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
