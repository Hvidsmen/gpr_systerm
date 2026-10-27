import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('LEGACY_WORK_BROWSER_TESTS')=='1','Optional admin work marker Chromium scenario')
class LegacyStructureBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_admin_bulk_marker_component_form_and_planner_visibility(self):
        from .test_legacy_structure import LegacyStructureTests
        LegacyStructureTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.admin)
        planner=Client();planner.force_login(self.planner)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'admin_cookie':self.client.cookies['sessionid'].value,
            'planner_cookie':planner.cookies['sessionid'].value,'catalogue':reverse('works:work_list'),'item_edit':reverse('works:work_item_update',args=[self.item.pk]),
            'item_detail':reverse('works:work_detail',args=[self.package.pk]),'work':self.simple.pk,'package':self.package.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/work_legacy_structure.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,LEGACY_WORK_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.simple.refresh_from_db();self.item.refresh_from_db()
        self.assertFalse(self.simple.is_legacy_structure);self.assertTrue(self.item.is_legacy_structure)
        print(result.stdout.strip())
