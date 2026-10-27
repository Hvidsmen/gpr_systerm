import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from . import test_decomposition as fixtures


@skipUnless(os.environ.get('PIVOT_INLINE_BROWSER_TESTS')=='1','Optional pivot Chromium scenario')
class PivotInlineBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_inline_totals_remain_visible_on_collapse(self):
        fixtures.DecompositionTests.setUpTestData.__func__(type(self))
        fixtures.DecompositionTests.setUp(self)
        client=Client();client.force_login(self.planner)
        config={'base':self.live_server_url,'url':reverse('planning:pivot_gpr_detail',args=[self.version.pk]),
            'cookie':client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/pivot_inline_totals.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],capture_output=True,text=True,
            env=dict(os.environ,PIVOT_INLINE_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
