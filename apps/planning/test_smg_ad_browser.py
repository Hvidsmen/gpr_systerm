import json,os,shutil,subprocess
from pathlib import Path
from uuid import uuid4
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core import signing
from django.urls import reverse
from apps.accounts.models import User,Role,AccessGroup
from . import test_workspace as fixtures
from .independent_smg import create
from .daily_schedule_table import add_entry,save_table
from .models import DailySchedule


@skipUnless(os.environ.get('SMG_AD_BROWSER_TESTS')=='1','Optional SMG variant and AD group browser flow')
class SMGADBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_copy_rename_and_ad_group_inheritance(self):
        fixtures.WorkspaceTests.setUpTestData.__func__(type(self))
        initial=create(self.planner,self.obj,fixtures.JAN,name='Начальный')
        entry=add_entry(self.planner,initial,{'kind':'work','work':self.simple})
        save_table(self.planner,initial,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-05','item_id':None,'field':'quantity','value':'7'}]}])
        admin=User.objects.create_user(username='copy-ad-browser-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        group=AccessGroup.objects.create(company=self.company,name='Планирование КТП',role=self.planner.role);group.assigned_objects.add(self.obj)
        identity={'guid':str(uuid4()),'login':'browser-ad-person','name':'Тестовый сотрудник','first_name':'Тестовый','last_name':'Сотрудник','email':'test@example.org'}
        token=signing.dumps({'guid':identity['guid'],'actor':admin.pk,'company':self.company.pk},salt='ad-add-user')
        self.client.force_login(admin)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'list':reverse('planning:daily_schedule_list'),'ad':reverse('accounts:add_from_ad')+'?selected='+token,
            'group':group.pk,'role':group.role_id,'object':self.obj.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/smg_ad_workflows.js'
        with patch('apps.accounts.directory_admin.search_directory',return_value=[identity]):
            result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,SMG_AD_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        initial.refresh_from_db();self.assertEqual(initial.name,'Переименованный')
        self.assertEqual(DailySchedule.objects.get(month=fixtures.JAN.replace(month=2)).name,'Февральский')
        added=User.objects.get(ldap_guid=identity['guid']);self.assertEqual(added.access_group,group)
        self.assertEqual(list(added.assigned_objects.all()),[self.obj]);print(result.stdout.strip())
