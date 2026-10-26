import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.accounts.models import User, Role
from apps.planning import test_workspace as fixtures
from apps.planning.models import LoadProfile, LoadProfileItem
from .models import ComplexObject, ProjectWorkItem


@skipUnless(os.environ.get('CATALOGUE_BROWSER_TESTS') == '1', 'Optional Playwright/Chromium check')
class CatalogueUXBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True
    def test_catalogue_search_context_bulk_menus_and_structure_edit(self):
        fixtures.WorkspaceTests.setUpTestData.__func__(type(self))
        self.obj.name = 'КТП';self.obj.save()
        self.composite.name = 'Монтаж системы видеонаблюдения'
        facility = ComplexObject.objects.create(company=self.company,construction_object=self.obj,
            section=self.composite.section,name='УЗА 26')
        self.composite.complex_object = facility;self.composite.save()
        profile = LoadProfile.objects.create(company=self.company,name='Равномерный профиль')
        LoadProfileItem.objects.create(company=self.company,profile=profile,workday_number=1,percentage=100)
        self.a.name = 'Укладка кабельной продукции';self.a.load_profile = profile;self.a.save()
        self.b.name = 'Монтаж оборудования';self.b.load_profile = profile;self.b.save()
        last = self.b
        for index in range(14):
            last = ProjectWorkItem.objects.create(company=self.company,project_work=self.composite,
                name=f'Дополнительная работа {index+1}',unit='м',load_profile=profile,
                sequence=index+3,quantity_per_unit=1,weight=0)
        observer = User.objects.create_user(username='catalogue-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        client = Client();client.force_login(self.planner)
        readonly = Client();readonly.force_login(observer)
        config = dict(base=self.live_server_url,list=reverse('works:work_list'),
            detail=reverse('works:work_detail',args=[self.composite.pk]),a=self.a.pk,b=self.b.pk,last=last.pk,
            facility=facility.pk,work=self.composite.pk,cookie=client.cookies['sessionid'].value,
            observerCookie=readonly.cookies['sessionid'].value,chromium=shutil.which('chromium'))
        script = Path(__file__).resolve().parents[2]/'tests/browser/catalogue_ux.js'
        result = subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,
            env=dict(os.environ,CATALOGUE_BROWSER_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.a.refresh_from_db();self.b.refresh_from_db()
        self.assertEqual((self.a.weight,self.b.weight),(25,75))
        self.assertEqual(self.a.quantity_per_unit,4)
        print(result.stdout.strip())
