import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from .test_independent_smg import IndependentSMGTests as Fixture
from . import independent_smg
from .models import DailySchedule


@skipUnless(os.environ.get('SMG_VARIANTS_BROWSER_TESTS')=='1','Optional SMG variants Chromium scenario')
class SMGVariantsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_create_compare_and_copy_independent_variants(self):
        Fixture.setUpTestData.__func__(type(self))
        from .test_workspace import JAN
        first=independent_smg.create(self.planner,self.obj,JAN,name='Исходный сценарий')
        second=independent_smg.create(self.planner,self.obj,JAN,name='Быстрый сценарий')
        Fixture.add(self,first,7);Fixture.add(self,second,12)
        self.client.force_login(self.planner)
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'create':reverse('planning:smg_create'),'list':reverse('planning:daily_schedule_list'),
            'compare':reverse('planning:smg_compare',args=[first.pk])+f'?other={second.pk}&differences=on','object':self.obj.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/smg_variants.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,SMG_VARIANTS_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(DailySchedule.objects.filter(construction_object=self.obj,month=JAN).count(),4)
        self.assertEqual(DailySchedule.objects.get(name='Независимый сценарий').entries.count(),0)
        self.assertEqual(DailySchedule.objects.get(name='Исходный сценарий — копия').entries.count(),1)
        print(result.stdout.strip())
