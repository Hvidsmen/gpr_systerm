import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .models import Equipment, EquipmentType
from .test_equipment_units_ux import EquipmentUnitsUXTests


@skipUnless(os.environ.get('EQUIPMENT_UNITS_BROWSER_TESTS')=='1', 'Optional physical-machines Chromium scenario')
class EquipmentUnitsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_create_planning_type_model_and_machine_search_and_mobile_card(self):
        EquipmentUnitsUXTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.user)
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,
            'chromium':shutil.which('chromium'),'catalogue':reverse('resources:equipment_type_list'),
            'machines':reverse('resources:equipment_unit_list'),'root_create':reverse('resources:equipment_type_create'),
            'unit_create':reverse('resources:equipment_unit_create')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/equipment_units.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],capture_output=True,text=True,
            env=dict(os.environ,EQUIPMENT_UNITS_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        machine=Equipment.objects.get(company=self.company,vin='UX-VIN')
        self.assertEqual(machine.inventory_number,'UX-INV')
        self.assertEqual(machine.type.brand_model,'UX MODEL')
        self.assertEqual(machine.type.parent.name,'Техника браузерной проверки')
        self.assertFalse(machine.is_active)
        self.assertEqual(EquipmentType.objects.filter(company=self.company,name='UX MODEL').count(),1)
        print(result.stdout.strip())
