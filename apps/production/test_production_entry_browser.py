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
        from apps.accounts.models import User,Role
        head=User.objects.create_user(username='browser-production-head',company=self.company,role=Role.objects.get(code='PRODUCTION_HEAD'))
        head_client=Client();head_client.force_login(head);head_cookie=head_client.cookies['sessionid'].value
        from tempfile import TemporaryDirectory
        from django.test import override_settings
        temporary=TemporaryDirectory();self.addCleanup(temporary.cleanup)
        setting=override_settings(DEBUG=True,ACTUATION_FILES_ROOT=Path(temporary.name)/'private');setting.enable();self.addCleanup(setting.disable)
        config={'base':self.live_server_url,'cookie':cookie,'second_cookie':second_cookie,'head_cookie':head_cookie,'reason_list':reverse('production:actuation_reason_reviews'),'chromium':shutil.which('chromium'),
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','employee':self.employee.pk,'work':self.work.pk,'second_work':self.second_work.pk,'reason':self.reason.pk,'editor':self.second.username,'history':reverse('production:fact_history'),
            'package':package.pk,'camera':camera.pk,'cable':cable.pk,'day':self.day.strftime('%d.%m.%Y'),'planned_position':planned_position.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/production_entry.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,PRODUCTION_ENTRY_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .dispatch_models import EmployeeActivity
        row=EmployeeActivity.objects.get(employee=self.employee,date=self.day)
        self.assertIn(row.work_id,{self.work.pk,self.second_work.pk});self.assertTrue(row.assignment_reviewed)
        self.assertEqual(set(row.work_links.values_list('work_id',flat=True)),{self.work.pk,self.second_work.pk})
        self.assertEqual(row.planning_position_id,planned_position.pk)
        self.assertEqual(row.position_id,self.employee.position_id)
        from .history_models import FactRevision
        from .models import DailyFact
        fact=DailyFact.objects.get(project_work=self.work,date=self.day)
        self.assertEqual(fact.reported_by_id,self.users['DISPATCHER'].pk)
        self.assertEqual(fact.actual_quantity,4)
        from decimal import Decimal
        revisions=FactRevision.objects.filter(entity_type='production.dailyfact',entity_id=str(fact.pk),operation='UPDATE',actor=self.second)
        self.assertTrue(any(Decimal(row.before['fields']['actual_quantity'])==5 and Decimal(row.after['fields']['actual_quantity'])==4 for row in revisions))
        self.assertEqual(DailyFact.objects.get(project_work=package,work_item=camera).actuation_type,'DAY')
        self.assertIn('Сильный ветер',DailyFact.objects.get(project_work=package,work_item=cable).actuation_reason)
        from .models import ActuationReason,ActuationDocument
        self.assertEqual(ActuationReason.objects.get(name='Сильный ветер').status,'APPROVED')
        self.assertEqual(ActuationReason.objects.get(name='Проблема с автобусом').status,'MERGED')
        self.assertEqual(ActuationReason.objects.get(name='Ошибка выбора причины').status,'REJECTED')
        self.assertEqual(ActuationDocument.objects.count(),4)
        self.assertEqual(ActuationDocument.objects.filter(removed_at__isnull=False).count(),1)
        from openpyxl import load_workbook
        workbook=load_workbook('/tmp/production-actuation-report.xlsx')
        self.assertTrue(any('Сильный ветер' in str(cell.value) for sheet in workbook for row in sheet for cell in row))
        print(result.stdout.strip())
