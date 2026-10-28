import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('LODGING_BROWSER_TESTS')=='1','Optional commandant accommodation Chromium scenario')
class LodgingBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_parallel_commandants_and_personnel_reconciliation(self):
        from .test_lodging import LodgingTests
        LodgingTests.setUpTestData.__func__(type(self))
        self.stay.arrived_on=self.day;self.stay.save()
        clients=[]
        for user in [self.commandant,self.colleague,self.hr]:
            client=Client();client.force_login(user);clients.append(client.cookies['sessionid'].value)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookies':clients,'board':reverse('personnel:lodging'),'sync':reverse('personnel:lodging_sync'),'housing':reverse('personnel:housing'),'camp':self.a.pk,'day':str(self.day),'employee':self.employee.pk,'second_employee':self.second_employee.pk,'bed':self.bed.pk,'bed2':self.bed2.pk,'bed3':self.bed3.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/lodging.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,LODGING_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        from .models import LodgingStay,LodgingEvent
        self.assertFalse(LodgingStay.objects.filter(departed_on=None,employee=self.second_employee).exists())
        self.assertEqual(LodgingEvent.objects.filter(employee=self.second_employee).count(),3)
        print(result.stdout.strip())
