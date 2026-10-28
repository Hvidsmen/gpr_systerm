import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('POSITION_INLINE_BROWSER_TESTS')=='1','Optional employee placement and totals Chromium scenario')
class PositionInlineBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_position_form_one_click_save_totals_and_graph_role(self):
        from .test_production_entry import ProductionEntryTests
        from apps.resources.models import Brigade
        from apps.resources.position_catalog import position_for
        from apps.planning.models import DailyScheduleEntry
        ProductionEntryTests.setUpTestData.__func__(type(self))
        brigade=Brigade.objects.create(company=self.company,name='Должность СМГ для назначения')
        role=position_for(brigade)
        DailyScheduleEntry.objects.create(schedule=self.schedule,key='labor:inline:role',kind='labor',label=brigade.name,rows=[{'date':str(self.day),'brigade_id':brigade.pk,'planned_count':'2'}])
        self.client.force_login(self.users['DISPATCHER'])
        config={'base':self.live_server_url,'url':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'employee':self.employee.pk,'role':role.pk,'role_name':role.name,'staff_role':str(self.employee.position)}
        script=Path(__file__).resolve().parents[2]/'tests/browser/position_inline.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,POSITION_INLINE_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .dispatch_models import EmployeeActivity
        row=EmployeeActivity.objects.get(employee=self.employee,date=self.day)
        self.assertEqual(row.planning_position_id,role.pk)
        self.assertEqual(row.position_id,self.employee.position_id)
        print(result.stdout.strip())
