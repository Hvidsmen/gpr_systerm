import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.accounts.models import User,Role
from apps.resources.models import EquipmentType
from . import test_subobject_smg as fixtures
from .daily_schedule_table import add_entry


@skipUnless(os.environ.get('SMG_BROWSER_TESTS')=='1','Optional Playwright/Chromium SMG scenario')
class SMGEditorBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_paste_save_links_equipment_type_and_mobile(self):
        fixtures.SubobjectSMGTests.setUpTestData.__func__(type(self))
        fixtures.SubobjectSMGTests.setUp(self)
        schedule=fixtures.SubobjectSMGTests.schedule(self)
        work=fixtures.SubobjectSMGTests.entry(self,schedule,self.simple)
        gear=add_entry(self.planner,schedule,{'kind':'equipment','equipment':self.equipment,'rate':20,'reference':''})
        variant=EquipmentType.objects.create(company=self.company,parent=self.equipment,name='Модель №101')
        observer=User.objects.create_user(username='smg-browser-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        def cookie(user):
            client=Client();client.force_login(user);return client.cookies['sessionid'].value
        config={'base':self.live_server_url,'detail':reverse('planning:daily_schedule_detail',args=[schedule.pk]),'list':reverse('planning:daily_schedule_list'),
            'graph':reverse('planning:work_resources_graph_smg',args=[schedule.pk]),'delete':reverse('planning:smg_delete',args=[schedule.pk]),
            'cookie':cookie(self.planner),'observer_cookie':cookie(observer),'work':work.pk,'gear':gear.pk,'equipment':self.equipment.pk,'variant':variant.pk,'local_work':work.metadata['id'],'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/smg_editor.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,env=dict(os.environ,SMG_BROWSER_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        work.refresh_from_db();gear.refresh_from_db()
        self.assertEqual(sum(fixtures.D(row['quantity']) for row in work.rows),31)
        self.assertEqual(sum(fixtures.D(row['planned_count']) for row in gear.rows),3)
        self.assertTrue(all(row['equipment_number']=='' for row in gear.rows))
        self.assertEqual(gear.graph_links.count(),1)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.snapshot)
        print(result.stdout.strip())
