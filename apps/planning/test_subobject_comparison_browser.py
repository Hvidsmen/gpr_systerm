import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.works.models import ComplexObject
from apps.resources.models import Brigade,BrigadeGroup,EquipmentType,EquipmentCategory
from . import test_subobject_smg as fixtures
from .models import GlobalPlanVersion
from .workspace_services import build_workspace_snapshot
from .daily_schedule_table import add_entry,save_table


@skipUnless(os.environ.get('SUBOBJECT_COMPARISON_BROWSER_TESTS')=='1','Optional comparison Chromium scenario')
class SubobjectComparisonBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    schedule=fixtures.SubobjectSMGTests.schedule
    entry=fixtures.SubobjectSMGTests.entry
    set_values=fixtures.SubobjectSMGTests.set_values

    def test_comparison_hierarchy_and_collapsed_totals(self):
        fixtures.SubobjectSMGTests.setUpTestData.__func__(type(self))
        fixtures.SubobjectSMGTests.setUp(self)
        facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.composite.section,name='УЗА 33')
        self.composite.complex_object=facility;self.composite.name='Монтаж системы видеонаблюдения';self.composite.save()
        self.simple.name='Самостоятельная работа';self.simple.save()
        self.a.name='Укладка кабеля';self.a.save();self.b.name='Монтаж оборудования';self.b.save()
        snapshot=build_workspace_snapshot(self.source,validate_inputs=False)
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(snapshot=snapshot)
        self.source.refresh_from_db()
        first=self.schedule();second=self.schedule(self.child2)
        self.set_values(self.entry(first,self.simple),60);self.set_values(self.entry(second,self.simple),40)
        self.set_values(self.entry(first,self.composite),20,self.a);self.set_values(self.entry(first,self.composite),30,self.b)
        specialized=EquipmentCategory.objects.create(company=self.company,name='Специализированная техника')
        auxiliary=EquipmentCategory.objects.create(company=self.company,name='Вспомогательная техника')
        self.equipment.category=specialized;self.equipment.save()
        extra=EquipmentType.objects.create(company=self.company,name='Вспомогательная машина',category=auxiliary)
        for label in ['Вспомогательный персонал','ИТР Состав']:
            group=BrigadeGroup.objects.create(company=self.company,name=label)
            brigade=Brigade.objects.create(company=self.company,name='Инженер' if label=='ИТР Состав' else 'Подсобный рабочий',group=group)
            entry=add_entry(self.planner,first,{'kind':'labor','brigade':brigade,'rate':0})
            self.fill_month(first,entry,2)
        for target,equipment,count in [(first,self.equipment,2),(second,self.equipment,1),(first,extra,1)]:
            entry=add_entry(self.planner,target,{'kind':'equipment','equipment':equipment,'rate':0})
            self.fill_month(target,entry,count)
        client=Client();client.force_login(self.planner)
        config={'base':self.live_server_url,'url':reverse('planning:daily_schedule_detail',args=[first.pk]),
            'cookie':client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/subobject_comparison.js'
        result=subprocess.run(['node',str(script)],cwd=script.parents[2],capture_output=True,text=True,
            env=dict(os.environ,SUBOBJECT_COMPARISON_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,snapshot)
        print(result.stdout.strip())

    def fill_month(self,schedule,entry,count):
        save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[
            {'date':row['date'],'item_id':None,'field':'planned_count','value':str(count)} for row in entry.rows]}])
