import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('TRANSPORT_BROWSER_TESTS')=='1','Optional transport Chromium scenario')
class TransportBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_movements_daily_use_concurrency_mobile_and_excel(self):
        from .test_transport import TransportTests
        TransportTests.setUpTestData.__func__(type(self))
        from . import transport
        transport.move(self.operator,self.camp,self.day,[self.machine.pk,self.second.pk],'ARRIVAL',signed=transport.snapshot(self.operator,self.camp,self.day))
        transport.save_day(self.operator,self.camp,self.day,[self.machine.pk,self.second.pk],'WORKED',signed=transport.snapshot(self.operator,self.camp,self.day))
        first=Client();first.force_login(self.operator)
        second=Client();second.force_login(self.colleague)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':first.cookies['sessionid'].value,'second_cookie':second.cookies['sessionid'].value,
            'board':self.board,'camp':self.camp.pk,'target':self.target.pk,'day':str(self.day),'machine':self.machine.pk,'second':self.second.pk,'work':self.work.pk,'type':self.type.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/transport.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,TRANSPORT_CONFIG=json.dumps(config)),timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .transport_models import EquipmentDayRecord,EquipmentTransportEvent
        self.assertEqual(EquipmentDayRecord.objects.filter(status='WORKED').count(),3)
        self.assertEqual(EquipmentTransportEvent.objects.filter(kind='TRANSFER').count(),1)
        print(result.stdout.strip())
