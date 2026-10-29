import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('TRANSPORT_REPAIR_BROWSER_TESTS')=='1','Optional transport repair workflow Chromium scenario')
class TransportRepairBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_separate_workflows_overlay_autosave_and_mobile(self):
        from . import test_transport as fixtures,transport
        from apps.resources.models import Employee
        fixtures.TransportTests.setUpTestData.__func__(type(self))
        transport.move(self.operator,self.camp,self.day,[self.machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(self.operator,self.camp,self.day))
        driver=Employee.objects.create(company=self.company,last_name='Тестов',first_name='Водитель')
        self.client.force_login(self.operator)
        config={'base':self.live_server_url,'url':self.board,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'camp':self.camp.pk,'day':str(self.day),'machine':self.machine.pk,'employee':driver.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/transport_repair.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,TRANSPORT_REPAIR_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .transport_details_models import EquipmentDayDetails
        from .history_models import FactRevision
        detail=EquipmentDayDetails.objects.get(equipment=self.machine,date=self.day)
        self.assertEqual(detail.shift_one_employee_id,driver.pk);self.assertEqual(detail.defects,'Проверить датчик давления')
        self.assertEqual(detail.request_details,'№ 123 · запчасти');self.assertEqual(detail.request_stage.name,'Проверено механиком')
        self.assertEqual(self.machine.transport_days.get(date=self.day).status,'WORKED')
        self.machine.transport_control.refresh_from_db();self.assertIsNone(self.machine.transport_control.locked_by_id)
        self.assertTrue(FactRevision.objects.filter(entity_type='production.equipmentdaydetails',entity_id=str(detail.pk),actor=self.operator,operation='UPDATE').exists())
        print(result.stdout.strip())
