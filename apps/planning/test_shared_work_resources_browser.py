import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from . import test_work_resources as fixtures
from .models import WorkMonthAllocation
from .independent_smg import create
from .resource_targets import identities


@skipUnless(os.environ.get('SHARED_RESOURCE_BROWSER_TESTS') == '1','Optional Playwright/Chromium scenario')
class SharedWorkResourceBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True
    prepare_owner = fixtures.WorkResourceTests.prepare_owner

    def test_gpr_shared_assignment_and_smg_picker(self):
        fixtures.WorkResourceTests.setUpTestData.__func__(type(self))
        fixtures.WorkResourceTests.setUp(self)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=self.composite,quantity=1)
        schedule = create(self.planner,self.obj,fixtures.JAN)
        self.prepare_owner(schedule)
        client = Client();client.force_login(self.manager)
        planner = Client();planner.force_login(self.planner)
        config = dict(base=self.live_server_url,gpr=reverse('planning:workspace_edit',args=[self.version.pk]),
            smg=reverse('planning:work_resources_smg',args=[schedule.pk]),cookie=client.cookies['sessionid'].value,
            gpr_cookie=planner.cookies['sessionid'].value,
            work=self.composite.pk,a=self.a.pk,b=self.b.pk,equipment=self.equipment.pk,
            chromium=shutil.which('chromium'))
        script = Path(__file__).resolve().parents[2]/'tests/browser/shared_work_resources.js'
        result = subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,
            env=dict(os.environ,SHARED_RESOURCE_BROWSER_CONFIG=json.dumps(config)),timeout=140)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        machine = self.version.resource_assignments.get(equipment_type=self.equipment)
        self.assertEqual(identities(machine),[(self.composite.pk,self.a.pk)])
        self.assertEqual(machine.count,3)
        fuel = self.version.resource_assignments.get(fuel_type='DIESEL')
        self.assertEqual(len(identities(fuel)),2)
        self.assertEqual(len(identities(schedule.resource_assignments.get())),2)
        print(result.stdout.strip())
