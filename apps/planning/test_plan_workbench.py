from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied,ValidationError
from apps.accounts.models import User,Role
from apps.resources.models import EquipmentType
from . import test_work_resources as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation,DailyScheduleEntry
from .independent_smg import create
from .daily_schedule_table import add_entry
from .plan_workbench import remove_smg_entry

JAN=fixtures.JAN

class PlanWorkbenchTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner
    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.composite,quantity=0,selected_item_ids=[self.a.pk],item_quantities={str(self.a.pk):'20'})
        self.url=reverse('planning:workspace_edit',args=[self.version.pk])+'?month=2026-01-01'
        self.schedule=create(self.planner,self.obj,JAN)
        self.smg_url=reverse('planning:daily_schedule_detail',args=[self.schedule.pk])

    def test_both_editors_render_same_toolbar_matrix_and_kind_order(self):
        for url,daily in [(self.url,False),(self.smg_url,True)]:
            response=self.client.get(url);self.assertEqual(response.status_code,200)
            self.assertTemplateUsed(response,'planning/_plan_workbench_toolbar.html')
            self.assertTemplateUsed(response,'planning/_plan_workbench_matrix.html')
            self.assertEqual([s['kind'] for s in response.context['plan_sections']],['work','labor','equipment','fuel'])
            self.assertEqual(response.context['plan_is_daily'],daily)
            self.assertContains(response,'Копировать таблицу в Excel');self.assertContains(response,'Связи работ и ресурсов')
            self.assertContains(response,'data-workbench-month-picker')
        response=self.client.get(self.url)
        self.assertEqual(len(response.context['plan_sections'][0]['groups'][0]['entries'][0].comparison),2)
        self.assertEqual(response.context['plan_sections'][0]['groups'][0]['entries'][0].comparison[1]['after'],'20',repr([(f.instance.item_quantities,f['edited_item_quantities'].value(),f['selected_item_ids'].value()) for f in response.context['work_forms']]))

    def test_same_resource_addition_creates_zero_rows_by_type_without_numbers(self):
        for url in [self.url,self.smg_url]:
            response=self.client.post(url,{'action':'add_table_resource','kind':'equipment','equipment':self.equipment.pk,'reference':'IGNORE','rate':'15'})
            self.assertEqual(response.status_code,302)
        row=self.version.resource_allocations.get(kind='equipment')
        self.assertEqual((row.count,row.hours,row.equipment_number),(0,0,''))
        entry=self.schedule.entries.get(kind='equipment')
        self.assertTrue(all(r['equipment_number']=='' and Decimal(r['planned_count'])==0 for r in entry.rows))
        variant=EquipmentType.objects.create(company=self.company,parent=self.equipment,name='Модель с номером')
        self.assertEqual(self.client.post(self.url,{'action':'add_table_resource','kind':'equipment','equipment':variant.pk}).status_code,400)
        self.assertEqual(self.version.resource_allocations.count(),1)

    def test_resource_add_reuses_existing_row_without_overwriting_values(self):
        row=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.equipment,count=3,hours=124,rate=55)
        self.client.post(self.url,{'action':'add_table_resource','kind':'equipment','equipment':self.equipment.pk,'rate':'99'})
        row.refresh_from_db();self.assertEqual((row.count,row.hours,row.rate),(3,124,55))
        self.assertEqual(self.version.resource_allocations.count(),1)

    def test_manager_same_surface_edits_resources_but_cannot_forge_work_changes_or_deletions(self):
        resource=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='labor',brigade=self.brigade,count=1)
        self.client.force_login(self.manager)
        response=self.client.get(self.url);self.assertEqual(response.status_code,200)
        self.assertFalse(response.context['plan_can_add_work']);self.assertTrue(response.context['plan_can_add_resources'])
        entry=response.context['plan_sections'][0]['groups'][0]['entries'][0];self.assertFalse(entry.can_edit)
        payload={'action':'save','works-TOTAL_FORMS':'1','works-INITIAL_FORMS':'1','works-0-id':self.row.pk,'works-0-work':self.composite.pk,'works-0-month':'2026-01-01','works-0-quantity':'99','works-0-DELETE':'on',
            'resources-TOTAL_FORMS':'1','resources-INITIAL_FORMS':'1','resources-0-id':resource.pk,'resources-0-month':'2026-01-01','resources-0-kind':'labor','resources-0-brigade':self.brigade.pk,'resources-0-count':'4','resources-0-hours':'124','resources-0-balance':'0','resources-0-liters':'0','resources-0-rate':'0'}
        response=self.client.post(self.url,payload);self.assertEqual(response.status_code,302)
        self.row.refresh_from_db();self.assertEqual(self.row.quantity,0);self.assertEqual(self.row.item_quantities,{str(self.a.pk):'20'})
        resource.refresh_from_db();self.assertEqual(resource.count,4)
        self.assertEqual(self.client.post(self.url,{'action':'remove_zero_works'}).status_code,403)

    def test_other_manager_and_unassigned_planner_cannot_write(self):
        other=User.objects.create_user(username='other-workbench-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.url,{'action':'add_table_resource','kind':'labor','brigade':self.brigade.pk}).status_code,403)
        self.client.force_login(self.planner);self.planner.assigned_objects.clear()
        self.assertEqual(self.client.post(self.url,{'action':'add_table_resource','kind':'labor','brigade':self.brigade.pk}).status_code,404)

    def test_common_smg_branch_adds_only_selected_work(self):
        response=self.client.post(self.smg_url,{'kind':'work','selection':'item:'+str(self.a.pk)})
        self.assertEqual(response.status_code,302)
        entry=self.schedule.entries.get(kind='work');self.assertEqual([i['id'] for i in entry.metadata['items']],[self.a.pk])
        self.assertTrue(all(Decimal(r['quantity'])==0 for r in entry.rows))

    def test_smg_row_removal_is_revision_checked_and_preserves_catalogue(self):
        entry=add_entry(self.planner,self.schedule,{'kind':'work','work':self.simple})
        with self.assertRaises(ValidationError):remove_smg_entry(self.planner,self.schedule,entry.pk,entry.revision-1)
        remove_smg_entry(self.planner,self.schedule,entry.pk,entry.revision)
        self.assertFalse(DailyScheduleEntry.objects.filter(pk=entry.pk).exists())
        self.simple.refresh_from_db()
        resource=add_entry(self.planner,self.schedule,{'kind':'labor','brigade':self.brigade,'rate':0})
        self.schedule.locked_before=date(2026,1,10);self.schedule.save()
        with self.assertRaises(ValidationError):remove_smg_entry(self.planner,self.schedule,resource.pk,resource.revision)

    def test_monthly_assignment_summary_counts_shared_resource_once(self):
        WorkMonthAllocation.objects.filter(pk=self.row.pk).update(quantity=1,selected_item_ids=[],item_quantities={})
        assignment=fixtures.WorkResourceTests.assignment(self,self.version,count=2,shifts_per_day=1,end_date=date(2026,1,31),include_crew=False)
        from . import resource_targets
        resource_targets.replace(assignment,[(self.composite.pk,self.a.pk),(self.composite.pk,self.b.pk)])
        response=self.client.get(self.url)
        equipment=response.context['plan_sections'][2]['groups'][0]['entries']
        self.assertEqual(len(equipment),1);self.assertEqual(equipment[0].comparison[0]['after'],2)

    def test_new_zero_work_is_available_for_linking_in_both_plans(self):
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.simple,quantity=0)
        add_entry(self.planner,self.schedule,{'kind':'work','work':self.simple})
        for kind,owner in [('gpr',self.version),('smg',self.schedule)]:
            response=self.client.get(reverse('planning:work_resources_graph_'+kind,args=[owner.pk]))
            self.assertEqual(response.status_code,200)
            self.assertIn('work:'+str(self.simple.pk),[node['token'] for node in response.context['resource_graph_data']['nodes']])

    def test_measure_order_is_shared_for_fuel_and_complex_work_items(self):
        self.a.sequence=2;self.a.save()
        self.row.selected_item_ids=[];self.row.item_quantities={};self.row.save()
        add_entry(self.planner,self.schedule,{'kind':'work','work':self.composite})
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='fuel',fuel_type='DIESEL')
        add_entry(self.planner,self.schedule,{'kind':'fuel','fuel':'DIESEL','reference':'','rate':0})
        gpr=self.client.get(self.url).context['plan_sections'];smg=self.client.get(self.smg_url).context['plan_sections']
        for index in [0,3]:
            first=gpr[index]['groups'][0]['entries'][0].comparison
            second=smg[index]['groups'][0]['entries'][0].comparison
            self.assertEqual([c['label'] for c in first],[c['label'] for c in second])
