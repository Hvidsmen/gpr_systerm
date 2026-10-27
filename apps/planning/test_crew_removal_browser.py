import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.resources.models import EquipmentCategory,EquipmentType,EquipmentCrewPosition
from . import test_work_resources as fixtures
from .independent_smg import create
from .daily_schedule_table import add_entry,save_table
from .plan_workbench import add_month_resource
from .table_resource_crew import sync_monthly


@skipUnless(os.environ.get('PLAN_WORKBENCH_BROWSER_TESTS')=='1','Optional Chromium scenario')
class CrewRemovalBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def test_reviewed_removal_and_equipment_catalogue(self):
        fixtures.WorkResourceTests.setUpTestData.__func__(type(self))
        fixtures.WorkResourceTests.setUp(self)
        category=EquipmentCategory.objects.create(company=self.company,name='Землеройная техника')
        other_category=EquipmentCategory.objects.create(company=self.company,name='Подъёмная техника')
        self.equipment.category=category;self.equipment.name='Экскаватор';self.equipment.save()
        other=EquipmentType.objects.create(company=self.company,name='Кран',category=other_category)
        for position in [self.position,self.helper]:
            EquipmentCrewPosition.objects.create(company=self.company,equipment_type=other,position=position,count=1)
        model=EquipmentType.objects.create(company=self.company,name='CAT 320',parent=self.equipment)
        EquipmentType.objects.create(company=self.company,name='Неактивная модель',parent=self.equipment,is_active=False)
        schedule=create(self.planner,self.obj,fixtures.JAN)
        for equipment in [self.equipment,other]:
            data={'kind':'equipment','equipment':equipment,'rate':0}
            add_month_resource(self.planner,self.version,fixtures.JAN,data)
            allocation=self.version.resource_allocations.get(kind='equipment',equipment_type=equipment)
            allocation.count=1;allocation.save()
            entry=add_entry(self.planner,schedule,data)
            save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':'1'}]}])
        sync_monthly(self.planner,self.version,fixtures.JAN)
        client=Client();client.force_login(self.planner)
        config={'base':self.live_server_url,'cookie':client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),
            'gpr':reverse('planning:workspace_edit',args=[self.version.pk]),'smg':reverse('planning:daily_schedule_detail',args=[schedule.pk]),
            'catalogue':reverse('resources:equipment_type_list'),'model':model.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/crew_removal.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],capture_output=True,text=True,
            env=dict(os.environ,CREW_REMOVAL_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertFalse(self.version.resource_allocations.filter(kind='equipment').exists())
        self.assertFalse(schedule.entries.filter(kind='equipment').exists())
        self.assertTrue(self.equipment.crew_positions.filter(position=self.position).exists())
        self.assertTrue(EquipmentType.objects.filter(pk=other.pk).exists())
        print(result.stdout.strip())
