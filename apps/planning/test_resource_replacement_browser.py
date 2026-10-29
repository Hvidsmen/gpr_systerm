import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.test import Client
from apps.accounts.models import User,Role
from apps.resources.models import EquipmentType,Position,Brigade
from apps.resources.position_catalog import brigade_for
from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
from . import test_resource_replacement as fixtures
from .models import ResourceMonthAllocation,WorkMonthAllocation
from .preparation import handoff


@skipUnless(os.environ.get('RESOURCE_REPLACE_BROWSER_TESTS')=='1','Optional resource replacement and estimator tree browser check')
class ResourceReplacementBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.ResourceReplacementTests.prepare_owner
    setUpData=fixtures.ResourceReplacementTests.setUp
    daily=fixtures.ResourceReplacementTests.daily

    def test_replace_in_both_editors_and_estimator_tree(self):
        fixtures.ResourceReplacementTests.setUpTestData.__func__(type(self));self.setUpData()
        old_people=self.version.resource_allocations.get(kind='labor',brigade_id=self.old_brigade_id);old_people.count+=3;old_people.save()
        other_position=Position.objects.create(company=self.company,name='Монтажник для замены',default_hourly_rate=55)
        other_brigade=Brigade.objects.get(pk=brigade_for(other_position));other_brigade.staffing_current=True;other_brigade.save()
        empty=EquipmentType.objects.create(company=self.company,name='Техника без экипажа')
        schedule,entry=self.daily()
        labor=schedule.entries.get(kind='labor',label=self.position.name)
        labor.rows[0]['planned_count']=labor.rows[0]['planned_workers']='7';labor.save()
        estimator=User.objects.create_user(username='replace-tree-estimator',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        facilities=[]
        for number in [32,33]:
            facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.simple.section,name='УЗА '+str(number));facilities.append(facility)
            work=ProjectWork.objects.create(company=self.company,section=self.simple.section,complex_object=facility,name='Монтаж системы',kind='COMPOSITE',unit='шт')
            ProjectWorkItem.objects.create(company=self.company,project_work=work,name='Прокладка кабеля '+str(number),unit='м',quantity_per_unit=2,weight=100)
            WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=work,quantity=2)
        handoff(self.planner,self.version,self.manager,estimator)
        task=self.version.estimate_reviews.order_by('-pk').first()
        self.client.force_login(self.planner);cookie=self.client.cookies['sessionid'].value
        estimator_client=Client();estimator_client.force_login(estimator);estimator_cookie=estimator_client.cookies['sessionid'].value
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':cookie,'estimatorCookie':estimator_cookie,
            'gpr':reverse('planning:workspace_edit',args=[self.version.pk]),'months':reverse('planning:workspace_months',args=[self.version.pk]),
            'smg':reverse('planning:daily_schedule_detail',args=[schedule.pk]),'review':reverse('planning:estimate_review_detail',args=[task.pk]),
            'target':self.target.pk,'targetLabel':self.target.name,'oldLabel':self.equipment.name,'laborLabel':self.position.name,'newLabor':other_brigade.pk,'empty':empty.pk,'facilities':[row.pk for row in facilities]}
        script=Path(__file__).resolve().parents[2]/'tests/browser/resource_replacement.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,RESOURCE_REPLACE_CONFIG=json.dumps(config)),timeout=160)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.allocation.refresh_from_db();entry.refresh_from_db();old_people.refresh_from_db();labor.refresh_from_db()
        self.assertEqual(self.allocation.equipment_type_id,empty.pk)
        self.assertEqual(entry.rows[0]['equipment_type_id'],self.target.pk)
        self.assertEqual(old_people.brigade_id,other_brigade.pk)
        self.assertEqual(labor.rows[0]['brigade_id'],other_brigade.pk)
        self.assertTrue(self.allocation.graph_links.filter(pk=self.link.pk).exists())
        print(result.stdout.strip())
