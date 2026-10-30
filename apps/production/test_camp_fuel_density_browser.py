import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .test_camp_fuel import CampFuelTests
from .camp_fuel_models import CampFuelDay

@skipUnless(os.environ.get('FUEL_DENSITY_BROWSER_TESTS')=='1','Optional fuel density browser scenario')
class FuelDensityBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_exact_preview_save_density_carry_and_mobile(self):
        CampFuelTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.operator)
        config=dict(base=self.live_server_url,cookie=self.client.cookies['sessionid'].value,chromium=shutil.which('chromium'),url=reverse('production:camp_fuel'),camp=self.camp.pk,day=str(self.day))
        script=Path(__file__).resolve().parents[2]/'tests/browser/fuel_density.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,FUEL_DENSITY_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(CampFuelDay.objects.count(),1)
        self.assertEqual(str(CampFuelDay.objects.get().closing_tonnes),'9.960')
        print(result.stdout.strip())
