import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.planning import test_workspace as fixtures


@skipUnless(os.environ.get('SHARES_BROWSER_TESTS')=='1','Optional Playwright/Chromium check')
class CostSharesBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True
    def test_inline_distribution_and_validation(self):
        fixtures.WorkspaceTests.setUpTestData.__func__(type(self))
        from apps.planning.models import LoadProfile, LoadProfileItem
        profile=LoadProfile.objects.create(company=self.company,name='Browser profile')
        LoadProfileItem.objects.create(company=self.company,profile=profile,workday_number=1,percentage=100)
        self.composite.items.update(load_profile=profile)
        client=Client();client.force_login(self.planner)
        config=dict(base=self.live_server_url,url=reverse('works:work_detail',args=[self.composite.pk]),
            cookie=client.cookies['sessionid'].value,a=self.a.pk,b=self.b.pk,chromium=shutil.which('chromium'))
        script=Path(__file__).resolve().parents[2]/'tests/browser/cost_shares.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,
            env=dict(os.environ,SHARES_BROWSER_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual((self.a.weight,self.b.weight),(25,75))
        print(result.stdout.strip())
