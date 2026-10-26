import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from datetime import date
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.accounts.models import User,Role
from . import test_work_resources as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation


@skipUnless(os.environ.get('RESOURCE_GRAPH_BROWSER_TESTS')=='1','Optional Playwright/Chromium scenario')
class ResourceGraphBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def test_connections_menu_drag_shared_counts_and_mobile(self):
        fixtures.WorkResourceTests.setUpTestData.__func__(type(self))
        fixtures.WorkResourceTests.setUp(self)
        self.obj.name='КТП · Ямал';self.obj.save()
        self.composite.name='Монтаж системы видеонаблюдения';self.composite.save()
        self.a.name='Укладка кабельной продукции';self.a.save()
        self.b.name='Монтаж оборудования';self.b.save()
        self.equipment.name='Экскаватор гусеничный';self.equipment.save()
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=self.composite,quantity=1)
        from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
        facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='УЗА 1')
        self.composite.complex_object=facility;self.composite.save()
        other_facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='УЗА 2')
        other=ProjectWork.objects.create(company=self.company,section=self.composite.section,complex_object=other_facility,name='Монтаж автоматики',unit='шт',kind='COMPOSITE')
        other_item=ProjectWorkItem.objects.create(company=self.company,project_work=other,name='Пусконаладочные работы',unit='шт',quantity_per_unit=1)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=other,quantity=1)
        legacy=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=self.equipment,count=4,hours=124)
        row=fixtures.WorkResourceTests.assignment(self,self.version,work=None,work_item=None,end_date=date(2026,1,31),shifts_per_day=1)
        fixtures.WorkResourceTests.assignment(self,self.version,equipment_type=None,position=self.helper,work=None,work_item=None,count=2,end_date=date(2026,1,31))
        def cookie(user):
            client=Client();client.force_login(user);return client.cookies['sessionid'].value
        role,_=Role.objects.get_or_create(code='OBSERVER',defaults={'name':'Наблюдатель'})
        observer=User.objects.create_user(username='graph-observer',company=self.company,role=role)
        config={'base':self.live_server_url,'graph':reverse('planning:work_resources_graph_gpr',args=[self.version.pk]),
            'cookie':cookie(self.manager),'planner_cookie':cookie(self.planner),'table':reverse('planning:workspace_edit',args=[self.version.pk]),'legacy':legacy.pk,'object1':facility.pk,'object2':other_facility.pk,'other_work':other.pk,'other_item':other_item.pk,'position':self.position.pk,'observer_cookie':cookie(observer),'assignment':row.pk,
            'work':self.composite.pk,'a':self.a.pk,'b':self.b.pk,'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/resource_graph.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],text=True,capture_output=True,
            env=dict(os.environ,RESOURCE_GRAPH_BROWSER_CONFIG=json.dumps(config)),timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(self.version.resource_assignments.filter(equipment_type=self.equipment).count(),1)
        row.refresh_from_db();self.assertEqual(row.count,3)
        self.assertEqual(row.extra_targets.count(),1)
        legacy.refresh_from_db();self.assertEqual(legacy.count,4);self.assertEqual(legacy.hours,124);self.assertEqual(legacy.graph_links.count(),2)
        fuel=self.version.resource_assignments.get(fuel_type='DIESEL');self.assertIsNone(fuel.work_id)
        print(result.stdout.strip())
