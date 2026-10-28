import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('TRANSPORT_SCROLL_BROWSER_TESTS')=='1','Optional transport scrollbar Chromium scenario')
class TransportScrollBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_top_scrollbar_sync_and_resize(self):
        from .test_transport import TransportTests
        from . import transport
        TransportTests.setUpTestData.__func__(type(self))
        transport.move(self.operator,self.camp,self.day,[self.machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(self.operator,self.camp,self.day))
        self.client.force_login(self.operator)
        config={'base':self.live_server_url,'url':self.board,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'camp':self.camp.pk,'day':str(self.day)}
        script=Path(__file__).resolve().parents[2]/'tests/browser/transport_scroll.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,TRANSPORT_SCROLL_CONFIG=json.dumps(config)),timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
