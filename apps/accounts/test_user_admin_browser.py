import json, os, shutil, subprocess
from pathlib import Path
from uuid import uuid4
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core import signing
from django.urls import reverse
from .models import User, Role, AccessGroup
from apps.planning import test_workspace as fixtures
from apps.planning.independent_smg import create
from apps.planning.daily_schedule_table import add_work_branch, save_table
from apps.planning.models import DailySchedule
from apps.works.models import ComplexObject


@skipUnless(os.environ.get('USER_ADMIN_BROWSER_TESTS')=='1', 'Optional admin and SMG tree browser check')
class UserAdminBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_admin_inheritance_mobile_and_smg_guides_in_edit_and_review(self):
        fixtures.WorkspaceTests.setUpTestData.__func__(type(self))
        self.composite.name = 'Монтаж системы автоматизированного управления электроснабжением и связи ВОЛС с длинным названием'
        self.composite.complex_object = ComplexObject.objects.create(company=self.company, construction_object=self.obj, section=self.composite.section, name='УЗА 32')
        self.composite.save()
        schedules=[]
        for title in ['Для заполнения','Для согласования']:
            schedule=create(self.planner,self.obj,fixtures.JAN,name=title)
            entries=add_work_branch(self.planner,schedule,'package:'+str(self.composite.pk))
            entry=entries[0]
            changes=[{'date':'2026-01-05','item_id':self.a.pk,'field':'quantity','value':'4'},{'date':'2026-01-05','item_id':self.b.pk,'field':'quantity','value':'6'}]
            save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':changes}])
            schedules.append(schedule)
        DailySchedule.objects.filter(pk=schedules[1].pk).update(status='SUBMITTED')
        admin = User.objects.create_user(username='user-admin-browser',company=self.company,role=Role.objects.get(code='ADMIN'))
        group=AccessGroup.objects.create(company=self.company,name='Склад КТП',role=Role.objects.get(code='FUEL_WAREHOUSE'))
        group.assigned_objects.add(self.obj)
        identity={'guid':str(uuid4()),'login':'ivanov.browser','name':'Иванов Иван','first_name':'Иван','last_name':'Иванов','email':'ivanov@example.org'}
        token=signing.dumps({'guid':identity['guid'],'actor':admin.pk,'company':self.company.pk},salt='ad-add-user')
        self.client.force_login(admin)
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
                'users':reverse('accounts:user_list'),'ad':reverse('accounts:add_from_ad')+'?selected='+token,
                'local':reverse('accounts:user_create'),'edit':reverse('accounts:user_access',args=[self.planner.pk]),
                'group':group.pk,'role':group.role_id,'object':self.obj.pk,'planner_role':self.planner.role_id,
                'smg':[reverse('planning:daily_schedule_detail',args=[schedule.pk]) for schedule in schedules]}
        script=Path(__file__).resolve().parents[2]/'tests/browser/user_admin.js'
        with patch('apps.accounts.directory_admin.search_directory', return_value=[identity]):
            result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,USER_ADMIN_CONFIG=json.dumps(config)),timeout=140)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        added=User.objects.get(ldap_guid=identity['guid'])
        self.assertEqual(added.role,group.role)
        self.assertEqual(list(added.assigned_objects.all()),[self.obj])
        print(result.stdout.strip())
