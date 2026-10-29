import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from .test_transport_planning import setup_data


@skipUnless(os.environ.get('TRANSPORT_PLANNING_BROWSER_TESTS')=='1','Optional grouped transport day-entry Chromium scenario')
class TransportPlanningBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_group_type_machine_assignment_and_inline_facts(self):
        from . import transport
        setup_data(type(self))
        for machine in [self.machine,self.second,self.bus]:
            transport.move(self.operator,self.camp,self.day,[machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(self.operator,self.camp,self.day))
        self.client.force_login(self.operator)
        config={'base':self.live_server_url,'url':self.board,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'camp':self.camp.pk,'day':str(self.day),'type':self.type.pk,'machine':self.machine.pk,'second':self.second.pk,'employee':self.employee.pk,'bus':self.bus.pk,'bus_type':self.bus_type.pk,'object':self.obj.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/transport_planning.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,TRANSPORT_PLANNING_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        record=self.machine.transport_days.get(date=self.day)
        self.assertEqual(record.status,'WORKED');self.assertEqual(record.construction_object_id,self.obj.pk)
        from .transport_details_models import EquipmentDayDetails
        self.assertEqual(EquipmentDayDetails.objects.get(equipment=self.machine,date=self.day).shift_one_employee_id,self.employee.pk)
        self.assertEqual(transport.condition_on(self.machine,self.day),'REPAIR')
        self.assertFalse(self.second.transport_days.filter(date=self.day).exists())
        print(result.stdout.strip())
