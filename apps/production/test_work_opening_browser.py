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
from .models import WorkOpeningBalance, DailyFact


@skipUnless(os.environ.get('WORK_OPENING_BROWSER_TESTS') == '1', 'Optional Chromium opening-balance workflow')
class WorkOpeningBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_administrator_can_edit_stock_and_dispatcher_cannot(self):
        fixtures.DispatcherTests.setUpTestData.__func__(type(self))
        def cookie(user):
            client = Client()
            client.force_login(user)
            return client.cookies['sessionid'].value
        config = {'base': self.live_server_url, 'chromium': shutil.which('chromium'), 'day': str(self.day),
            'list': reverse('production:work_opening_list'), 'work': self.work.pk, 'object': self.obj.pk,
            'edit': reverse('production:work_opening_edit', args=[self.work.pk]),
            'fact': self.url + f'?construction_object={self.obj.pk}&date={self.day}&section=works',
            'admin': cookie(self.users['ADMIN']), 'dispatcher': cookie(self.users['DISPATCHER'])}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/work_opening.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True,
            env=dict(os.environ, WORK_OPENING_CONFIG=json.dumps(config)), timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        self.assertEqual(WorkOpeningBalance.objects.get().quantity, 125)
        self.assertFalse(DailyFact.objects.exists())
        print(result.stdout.strip())
