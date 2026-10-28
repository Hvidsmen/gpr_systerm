import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse


@skipUnless(os.environ.get('PRODUCTION_ENTRY_BROWSER_TESTS')=='1','Optional production fact Chromium scenario')
class ProductionEntryBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_parallel_claims_four_tabs_report_and_graph(self):
        from .test_production_entry import ProductionEntryTests
        ProductionEntryTests.setUpTestData.__func__(type(self))
        from apps.resources.models import Brigade
        from apps.resources.position_catalog import position_for
        from apps.planning.models import DailyScheduleEntry
        planned_brigade=Brigade.objects.create(company=self.company,name='Должность из СМГ для замещения')
        planned_position=position_for(planned_brigade)
        DailyScheduleEntry.objects.create(schedule=self.schedule,key='labor:replacement',kind='labor',label=planned_brigade.name,
            rows=[{'date':str(self.day),'brigade_id':planned_brigade.pk,'planned_count':'2'}])
        from .test_work_entry_ui import hierarchy_fixture
        complex_object,package,camera,cable,_,_=hierarchy_fixture(self)
        self.client.force_login(self.users['DISPATCHER']);cookie=self.client.cookies['sessionid'].value
        from django.test import Client
        other_client=Client();other_client.force_login(self.second);second_cookie=other_client.cookies['sessionid'].value
        config={'base':self.live_server_url,'cookie':cookie,'second_cookie':second_cookie,'chromium':shutil.which('chromium'),
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','employee':self.employee.pk,'work':self.work.pk,'second_work':self.second_work.pk,'reason':self.reason.pk,'editor':self.second.username,'history':reverse('production:fact_history'),
            'package':package.pk,'camera':camera.pk,'cable':cable.pk,'day':self.day.strftime('%d.%m.%Y'),'planned_position':planned_position.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_entry.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,PRODUCTION_ENTRY_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .dispatch_models import EmployeeActivity
        row=EmployeeActivity.objects.get(employee=self.employee,date=self.day)
        self.assertEqual(row.work_id,self.work.pk);self.assertTrue(row.assignment_reviewed)
        self.assertEqual(row.planning_position_id,planned_position.pk)
        self.assertEqual(row.position_id,self.employee.position_id)
        from .dispatch_models import ProductionEntryDay
        self.assertIsNone(ProductionEntryDay.objects.get(dispatcher=self.users['DISPATCHER']).confirmed_at, 'A colleague correction invalidates the earlier confirmation')
        from .history_models import FactRevision
        from .models import DailyFact
        fact=DailyFact.objects.get(project_work=self.work,date=self.day)
        self.assertEqual(fact.reported_by_id,self.users['DISPATCHER'].pk)
        self.assertEqual(fact.actual_quantity,4)
        from decimal import Decimal
        revisions=FactRevision.objects.filter(entity_type='production.dailyfact',entity_id=str(fact.pk),operation='UPDATE',actor=self.second)
        self.assertTrue(any(Decimal(row.before['fields']['actual_quantity'])==5 and Decimal(row.after['fields']['actual_quantity'])==4 for row in revisions))
        print(result.stdout.strip())
