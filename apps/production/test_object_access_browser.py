import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from django.test import Client


@skipUnless(os.environ.get('OBJECT_ACCESS_BROWSER_TESTS')=='1','Optional production object access browser scenario')
class ProductionObjectAccessBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_admin_separate_scope_and_dispatcher_read_only_day(self):
        from .test_object_access import ProductionObjectAccessTests
        from apps.accounts.models import AccessGroup
        from .dispatch_models import FactSectionDay,ProductionEntryDay
        ProductionObjectAccessTests.setUpTestData.__func__(type(self))
        user=self.users['DISPATCHER']
        group=AccessGroup.objects.create(company=self.company,name='Группа КТП-1',role=user.role)
        group.assigned_objects.add(self.obj);group.production_view_objects.add(self.other)
        admin_client=Client();admin_client.force_login(self.users['ADMIN']);admin_cookie=admin_client.cookies['sessionid'].value
        self.client.force_login(user);user_cookie=self.client.cookies['sessionid'].value
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'admin_cookie':admin_cookie,'cookie':user_cookie,
            'access':reverse('accounts:user_access',args=[user.pk]),'group':group.pk,'role':user.role_id,'object':self.obj.pk,'view_object':self.other.pk,
            'path':f'{self.url}?construction_object={self.other.pk}&date={self.day}&section=works&entry_step=3',
            'fill_path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works',
            'report':reverse('production:smg_period_detail')+f'?construction_object={self.other.pk}&date={self.day}'}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/production_object_access.js')],capture_output=True,text=True,env=dict(os.environ,OBJECT_ACCESS_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertSetEqual(set(user.assigned_objects.all()),{self.obj})
        self.assertSetEqual(set(user.production_view_objects.all()),{self.other})
        self.assertFalse(FactSectionDay.objects.filter(construction_object=self.other).exists())
        self.assertFalse(ProductionEntryDay.objects.filter(construction_object=self.other,dispatcher=user).exists())
        print(result.stdout.strip())
