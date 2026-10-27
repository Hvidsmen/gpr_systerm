import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.accounts.models import Role,User
from apps.resources.models import Brigade,BrigadeGroup,EquipmentType,EquipmentCategory
from apps.resources.position_catalog import brigade_for
from apps.resources.equipment_merge import merge_equipment
from apps.works.models import ComplexObject
from . import test_work_resources as fixtures
from .approval_test_helpers import estimator_check,departments
from .approval_workflow import transition
from .models import WorkMonthAllocation,ResourceMonthAllocation


@skipUnless(os.environ.get('APPROVED_GROUPS_BROWSER_TESTS')=='1','Optional approved-group Chromium scenario')
class ApprovedGroupsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def test_groups_match_before_and_after_approval_and_snapshot_stays_frozen(self):
        fixtures.WorkResourceTests.setUpTestData.__func__(type(self));fixtures.WorkResourceTests.setUp(self)
        facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.composite.section,name='УЗА 33')
        self.composite.complex_object=facility;self.composite.save()
        for work,quantity in [(self.simple,5),(self.composite,10)]:
            WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=work,quantity=quantity)
        specialized=EquipmentCategory.objects.create(company=self.company,name='Специализированная техника')
        auxiliary=EquipmentCategory.objects.create(company=self.company,name='Вспомогательная техника')
        old=EquipmentCategory.objects.create(company=self.company,name='Борт.АТ, фург. общие, реф.')
        self.equipment.name=old.name;self.equipment.category=old;self.equipment.save()
        canonical=EquipmentType.objects.create(company=self.company,name=old.name,category=auxiliary)
        snow=EquipmentType.objects.create(company=self.company,name='Снегоболотоход',category=specialized)
        for equipment,count in [(self.equipment,1),(snow,2)]:
            fixtures.WorkResourceTests.assignment(self,self.version,equipment_type=equipment,count=count,include_crew=False,end_date=fixtures.JAN.replace(day=31),shifts_per_day=1)
        merge_equipment(self.planner,canonical.pk,[self.equipment.pk])
        itr=BrigadeGroup.objects.create(company=self.company,name='ИТР Состав')
        brigade=Brigade.objects.get(pk=brigade_for(self.position));brigade.group=itr;brigade.save()
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='labor',brigade=brigade,count=1)
        admin=User.objects.create_user(username='approved-group-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        client=Client();client.force_login(admin)
        config={'base':self.live_server_url,'url':reverse('planning:workspace_edit',args=[self.version.pk]),'cookie':client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        self.run_browser({**config,'stage':'draft'})
        # Capture the old classifications, then reproduce catalogue changes made
        # after approval while retaining the approved numbers and names.
        canonical.category=old;canonical.save();snow.category=old;snow.save();brigade.group=None;brigade.save()
        estimator_check(self.version,self.planner)
        self.version=transition(self.version,self.manager,'submit')
        self.version=transition(departments(self.version,self.approvers),self.approvers['CEO'],'approve')
        saved=deepcopy(self.version.snapshot)
        canonical.category=auxiliary;canonical.save();snow.category=specialized;snow.save();brigade.group=itr;brigade.save()
        self.run_browser({**config,'stage':'approved'})
        self.version.refresh_from_db();self.assertEqual(self.version.snapshot,saved)

    def run_browser(self,config):
        script=Path(__file__).resolve().parents[2]/'tests/browser/approved_groups.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],capture_output=True,text=True,
            env=dict(os.environ,APPROVED_GROUPS_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
