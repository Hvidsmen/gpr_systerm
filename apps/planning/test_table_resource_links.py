from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.db.models.deletion import ProtectedError
from . import test_work_resources as fixtures,resource_links,preparation
from .models import ResourceMonthAllocation,ResourceWorkLink,WorkMonthAllocation,GlobalPlanVersion
from .workspace_services import build_workspace_snapshot
from .independent_smg import create
from .resource_graph import target_token


class TableResourceLinkTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.client.force_login(self.manager)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=self.composite,quantity=1)
        self.row=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=self.equipment,count=3,hours=124,rate=500)
        self.tokens=[target_token(self.composite.pk,i.pk) for i in [self.a,self.b]]
        self.url=reverse('planning:work_resources_links_gpr',args=[self.version.pk])

    def post(self,tokens=None,**kwargs):
        self.row.refresh_from_db()
        return self.client.post(self.url,{'assignment_id':'allocation:'+str(self.row.pk),'revision':resource_links.revision(self.row),'targets':self.tokens if tokens is None else tokens,**kwargs},content_type='application/json')

    def test_link_existing_table_resource_preserves_all_values_and_is_visible(self):
        before=build_workspace_snapshot(self.version)['resources']['equipment']
        signature=preparation.resource_signature(self.version)
        response=self.post();self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(response.json()['assignment']['targets']),2)
        self.row.refresh_from_db();self.assertEqual((self.row.count,self.row.hours,self.row.rate),(3,124,500))
        after=build_workspace_snapshot(self.version)['resources']['equipment']
        self.assertEqual(sum(Decimal(r['planned_count']) for r in before),sum(Decimal(r['planned_count']) for r in after))
        self.assertEqual(sum(Decimal(r['planned_machine_hours']) for r in after),124)
        self.assertEqual(len(after),31);self.assertTrue(all(len(r['work_targets'])==2 for r in after))
        self.assertNotEqual(preparation.resource_signature(self.version),signature)
        graph=self.client.get(reverse('planning:work_resources_graph_gpr',args=[self.version.pk]))
        shown=next(r for r in graph.context['resource_editor_data']['assignments'] if r['id']=='allocation:'+str(self.row.pk))
        self.assertIn('?legacy=1&month=',shown['open_table_url'])
        self.assertEqual(self.post([]).status_code,200);self.assertFalse(self.row.graph_links.exists())

    def test_cross_complex_objects_and_packages_with_one_resource(self):
        from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
        first=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='УЗА 1')
        second=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='УЗА 2')
        self.composite.complex_object=first;self.composite.save()
        work=ProjectWork.objects.create(company=self.company,section=self.composite.section,complex_object=second,name='Другой комплекс',kind='COMPOSITE',unit='шт')
        child=ProjectWorkItem.objects.create(company=self.company,project_work=work,name='Другая работа',unit='шт',quantity_per_unit=1)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=work,quantity=1)
        self.assertEqual(self.post([self.tokens[0],target_token(work.pk,child.pk)]).status_code,200)
        self.assertEqual(self.row.graph_links.count(),2);self.assertEqual(self.version.resource_allocations.count(),1)
        with self.assertRaises(ProtectedError):child.delete()

    def test_fuel_and_frozen_sources_cannot_get_links(self):
        self.row.kind='fuel';self.row.equipment_type=None;self.row.fuel_type='DIESEL';self.row.count=0;self.row.hours=0;self.row.liters=10;self.row.save()
        self.assertEqual(self.post().status_code,400)
        self.row.kind='equipment';self.row.equipment_type=self.equipment;self.row.fuel_type='';self.row.liters=0;self.row.count=3;self.row.save()
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='APPROVED')
        self.assertEqual(self.post().status_code,400);self.assertFalse(self.row.graph_links.exists())

    def test_stale_revision_and_extra_numbers_do_not_change_table_resource(self):
        revision=resource_links.revision(self.row)
        self.row.count=5;self.row.save()
        self.assertEqual(self.post(revision=revision).status_code,409)
        self.assertEqual(self.post(count=9).status_code,400)
        self.assertFalse(self.row.graph_links.exists())

    def test_smg_table_resource_links_change_only_metadata(self):
        schedule=create(self.planner,self.obj,fixtures.JAN);self.prepare_owner(schedule)
        entry=schedule.entries.create(kind='equipment',key='equipment:table',label='Экскаватор',rows=[{'date':'2026-01-01','equipment_type_id':self.equipment.pk,'planned_count':'3','planned_machine_hours':'8'}])
        # Add saved work rows to the independent SMG.
        schedule.entries.create(kind='work',key='work:'+str(self.composite.pk),label=self.composite.name,metadata={'id':self.composite.pk,'kind':'COMPOSITE','items':[{'id':i.pk,'name':i.name} for i in [self.a,self.b]]},rows=[{'date':'2026-01-01','item_id':self.a.pk,'quantity':'1'}])
        url=reverse('planning:work_resources_links_smg',args=[schedule.pk])
        response=self.client.post(url,{'assignment_id':'entry:'+str(entry.pk),'revision':resource_links.revision(entry),'targets':self.tokens},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        entry.refresh_from_db();self.assertEqual(entry.rows[0]['planned_count'],'3');self.assertEqual(entry.rows[0]['planned_machine_hours'],'8')
        self.assertEqual(len(entry.rows[0]['work_targets']),2)
        schedule.locked_before=date(2026,1,2);schedule.save()
        response=self.client.post(url,{'assignment_id':'entry:'+str(entry.pk),'revision':resource_links.revision(entry),'targets':[]},content_type='application/json')
        self.assertEqual(response.status_code,400)

    def test_copy_to_smg_keeps_table_links_and_original_amounts(self):
        self.assertEqual(self.post().status_code,200)
        snapshot=build_workspace_snapshot(self.version)
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='APPROVED',snapshot=snapshot)
        self.version.refresh_from_db()
        schedule=create(self.planner,self.obj,fixtures.JAN,source=self.version,copy_from_gpr=True)
        entry=schedule.entries.get(kind='equipment')
        self.assertEqual(entry.graph_links.count(),2)
        self.assertEqual(sum(Decimal(r['planned_machine_hours']) for r in entry.rows),124)
        self.assertTrue(all(r['planned_count']=='3' or r['planned_count']==3 for r in entry.rows))
