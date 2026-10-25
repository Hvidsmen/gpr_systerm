import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from . import test_dispatch as fixtures
from .models import EquipmentAssignment, EquipmentStateChange, EquipmentActivity, FactSectionDay


@skipUnless(os.environ.get('EQUIPMENT_BROWSER_TESTS') == '1', 'Optional Playwright/Chromium check')
class EquipmentOperationsBrowserTests(StaticLiveServerTestCase):
    def test_assign_confirm_repair_and_daily_exception(self):
        fixtures.DispatcherTests.setUpTestData.__func__(type(self))
        client = Client(); client.force_login(self.users['MECHANIZATION_DISPATCHER'])
        config = dict(base=self.live_server_url, day=str(self.day), object=self.obj.pk,
            machine=self.machine.pk, work=self.work.pk, cookie=client.cookies['sessionid'].value,
            fact=reverse('production:fact_day_workspace'), assignments=reverse('production:equipment_assignment_list'),
            chromium=shutil.which('chromium'))
        script = Path(__file__).resolve().parents[2]/'tests/browser/equipment_operations.js'
        result = subprocess.run(['node', str(script)], cwd=script.parents[2], capture_output=True, text=True,
            env=dict(os.environ, EQUIPMENT_BROWSER_CONFIG=json.dumps(config)), timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)
        self.assertEqual(EquipmentAssignment.objects.count(), 1)
        self.assertEqual(EquipmentStateChange.objects.count(), 2)
        self.assertEqual(list(EquipmentActivity.objects.order_by('date').values_list('state', flat=True)), ['WORKING', 'REPAIR', 'IDLE'])
        self.assertEqual(FactSectionDay.objects.filter(confirmed_at__isnull=False).count(), 3)
        print(result.stdout.strip())
