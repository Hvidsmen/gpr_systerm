import json,os,subprocess,shutil
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

@skipUnless(os.environ.get('PIVOT_SOURCES_BROWSER_TESTS')=='1','Optional source selector and readable pivot check')
class PivotSourceBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_source_selection_readable_table_and_mobile(self):
        from .test_decomposition import DecompositionTests
        from .models import DailySchedule,DailyScheduleEntry
        from datetime import date
        DecompositionTests.setUpTestData.__func__(type(self));DecompositionTests.setUp(self)
        schedule=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=date(2026,1,1),revision=1,status='APPLIED',created_by=self.planner)
        DailyScheduleEntry.objects.create(schedule=schedule,key='work:'+str(self.simple.pk),kind='work',label=self.simple.name,metadata=next(spec for spec in self.snapshot['works'] if spec['id']==self.simple.pk),rows=[{'date':'2026-01-05','item_id':None,'quantity':'777'}])
        self.client.force_login(self.planner)
        config={'base':self.live_server_url,'url':reverse('planning:pivot_report_list')+f'?plan_type=gpr&plan_version={self.version.pk}','cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'smg':schedule.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/pivot_sources.js'
        result=subprocess.run(['node',str(script)],text=True,capture_output=True,env=dict(os.environ,PIVOT_SOURCES_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
