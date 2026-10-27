import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from . import test_work_resources as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation
from .independent_smg import create
from .daily_schedule_table import add_entry

@skipUnless(os.environ.get('PLAN_WORKBENCH_BROWSER_TESTS')=='1','Optional shared workbench browser scenario')
class PlanWorkbenchBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.WorkResourceTests.prepare_owner
    def test_same_editing_pattern_gpr_smg_and_excel_copy(self):
        fixtures.WorkResourceTests.setUpTestData.__func__(type(self));fixtures.WorkResourceTests.setUp(self)
        self.composite.name='Монтаж системы';self.composite.save()
        self.a.name='Кабель';self.a.save();self.b.name='Оборудование';self.b.save()
        row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=self.composite,quantity=0,selected_item_ids=[self.a.pk],item_quantities={str(self.a.pk):'20'})
        gear=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=self.equipment,count=2,hours=16)
        schedule=create(self.planner,self.obj,fixtures.JAN)
        work=add_entry(self.planner,schedule,{'kind':'work','work':self.simple})
        def cookie(user):
            client=Client();client.force_login(user);return client.cookies['sessionid'].value
        config={'base':self.live_server_url,'gpr':reverse('planning:workspace_edit',args=[self.version.pk]),'smg':reverse('planning:daily_schedule_detail',args=[schedule.pk]),
            'cookie':cookie(self.planner),'manager_cookie':cookie(self.manager),'a':self.a.pk,'b':self.b.pk,'brigade':self.brigade.pk,'work_entry':work.pk,'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/plan_workbench.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,env=dict(os.environ,PLAN_WORKBENCH_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        row.refresh_from_db();self.assertEqual(row.quantity,10)
        self.assertEqual(row.item_quantities,{str(self.a.pk):'42',str(self.b.pk):'30'})
        gear.refresh_from_db();self.assertEqual(gear.count,3);self.assertEqual(gear.hours,16)
        print(result.stdout.strip())
