import json,os,shutil,subprocess
from pathlib import Path
from datetime import date,timedelta
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_configured_plan_groups as fixtures
from .models import ResourceMonthAllocation
from .independent_smg import create
from .daily_schedule_table import add_entry,save_table


@skipUnless(os.environ.get('CONFIGURED_GROUPS_BROWSER_TESTS')=='1','Optional configured group order and totals Chromium scenario')
class ConfiguredGroupsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.ConfiguredPlanGroupTests.prepare_owner
    def test_shared_group_order_and_live_collapsed_totals_in_gpr_and_smg(self):
        fixtures.ConfiguredPlanGroupTests.setUpTestData.__func__(type(self));fixtures.ConfiguredPlanGroupTests.setUp(self)
        labor,equipment=fixtures.ConfiguredPlanGroupTests.setup_resources(self)
        self.version.end_date=date(2026,2,28);self.version.save(update_fields=['end_date'])
        schedule=create(self.planner,self.obj,date(2026,1,1))
        for kind,items in [('labor',labor),('equipment',equipment)]:
            for item,count in items:
                ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=date(2026,1,1),kind=kind,count=count,
                    **({'brigade':item} if kind=='labor' else {'equipment_type':item}))
                entry=add_entry(self.planner,schedule,{'kind':kind,'brigade' if kind=='labor' else 'equipment':item,'rate':0,'add_crew':False})
                save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[
                    {'date':(date(2026,1,1)+timedelta(days=i)).isoformat(),'item_id':None,'field':'planned_count','value':str(count)} for i in range(31)]}])
        self.client.force_login(self.planner)
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'order':reverse('resources:brigade_group_list'),'single':reverse('planning:workspace_edit',args=[self.version.pk]),
            'months':reverse('planning:workspace_months',args=[self.version.pk]),'smg':reverse('planning:daily_schedule_detail',args=[schedule.pk])}
        script=Path(__file__).resolve().parents[2]/'tests/browser/configured_groups.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,CONFIGURED_GROUPS_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
