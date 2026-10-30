import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

@skipUnless(os.environ.get('GROUPS_BROWSER_TESTS')=='1','Optional groups and crew browser check')
class GroupsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_groups_and_machine_crew_and_admin_role_filter(self):
        from apps.production.test_transport import TransportTests
        from .models import AccessGroup,Role
        from apps.resources.models import Brigade
        from apps.resources.position_catalog import position_for
        TransportTests.setUpTestData.__func__(type(self))
        group=AccessGroup.objects.create(company=self.company,name='Группа механизации',role=Role.objects.get(code='MECHANIZATION_DISPATCHER'))
        one=position_for(Brigade.objects.create(company=self.company,name='Экипаж водитель'));two=position_for(Brigade.objects.create(company=self.company,name='Экипаж машинист'))
        self.client.force_login(self.admin)
        config={'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'groups':reverse('accounts:access_groups'),'edit':reverse('accounts:access_group_edit',args=[group.pk]),'transport':reverse('production:transport_board')+f'?camp={self.camp.pk}&date={self.day}&tab=state','tasks':reverse('planning:task_list'),'ad_search':reverse('accounts:ad_group_search'),'crew':[one.pk,two.pk]}
        script=Path(__file__).resolve().parents[2]/'tests/browser/groups_crew.js'
        result=subprocess.run(['node',str(script)],text=True,capture_output=True,env=dict(os.environ,GROUPS_CONFIG=json.dumps(config)),timeout=110)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
