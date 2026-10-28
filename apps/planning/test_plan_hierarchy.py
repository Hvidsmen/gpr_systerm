from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.projects.models import Section
from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
from . import test_work_resources as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation
from .independent_smg import create
from .daily_schedule_table import add_entry,add_work_branch

JAN=fixtures.JAN

class PlanHierarchyTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner
    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.section=self.composite.section;self.section.name='Раздел А';self.section.save()
        self.facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.section,name='УЗА 1')
        self.composite.complex_object=self.facility;self.composite.save()
        self.allocation=WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.composite,quantity=0,selected_item_ids=[self.a.pk],item_quantities={str(self.a.pk):'20'})
        self.schedule=create(self.planner,self.obj,JAN)
        self.entry=add_work_branch(self.planner,self.schedule,'item:'+str(self.a.pk))[0]
        self.gpr=reverse('planning:workspace_edit',args=[self.version.pk]);self.smg=reverse('planning:daily_schedule_detail',args=[self.schedule.pk])

    def tree(self,url):return self.client.get(url).context['plan_sections'][0]['hierarchy']

    def test_both_editors_keep_section_object_package_and_selected_subwork(self):
        for url in [self.gpr,self.smg]:
            tree=self.tree(url);self.assertEqual(len(tree),1);self.assertEqual(tree[0]['label'],'Раздел А')
            obj=tree[0]['objects'][0];self.assertEqual(obj['label'],'УЗА 1')
            branch=obj['branches'][0];self.assertTrue(branch['is_package']);self.assertEqual(branch['count'],1)
            entry=branch['entries'][0];self.assertTrue(entry.incomplete)
            self.assertIn('Раздел А → УЗА 1',entry.hierarchy_path)
            self.assertEqual([row['item_id_value'] for row in entry.comparison if row['item_id_value']],[self.a.pk])
        self.allocation.refresh_from_db();self.assertEqual(self.allocation.item_quantities,{str(self.a.pk):'20'});self.assertEqual(self.allocation.quantity,0)

    def test_identical_section_and_package_names_do_not_merge_identities(self):
        second=Section.objects.create(company=self.company,construction_object=self.obj,name=self.section.name)
        other_facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=second,name='УЗА 2')
        same=ProjectWork.objects.create(company=self.company,section=self.section,complex_object=self.facility,name=self.composite.name,unit='шт',kind='COMPOSITE')
        ProjectWorkItem.objects.create(company=self.company,project_work=same,name='Другая работа',unit='шт',quantity_per_unit=1)
        separate=ProjectWork.objects.create(company=self.company,section=second,complex_object=other_facility,name=self.composite.name,unit='шт',kind='COMPOSITE')
        ProjectWorkItem.objects.create(company=self.company,project_work=separate,name='Работа другого раздела',unit='шт',quantity_per_unit=1)
        for work in [same,separate]:
            WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=work,quantity=1)
            add_entry(self.planner,self.schedule,{'kind':'work','work':work})
        for url in [self.gpr,self.smg]:
            tree=self.tree(url);self.assertEqual(len(tree),2);self.assertEqual(len({node['key'] for node in tree}),2)
            branches=[branch for node in tree for obj in node['objects'] for branch in obj['branches']]
            self.assertEqual(len(branches),3);self.assertEqual(len({b['key'] for b in branches}),3)
            first=next(node for node in tree if node['key']=='section:'+str(self.section.pk))
            self.assertEqual(len(first['objects'][0]['branches']),2)

    def test_standalone_work_has_no_fake_package_level(self):
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.simple,quantity=5)
        add_entry(self.planner,self.schedule,{'kind':'work','work':self.simple})
        for url in [self.gpr,self.smg]:
            obj=next(obj for root in self.tree(url) for obj in root['objects'] if not obj['has_object'])
            self.assertFalse(obj['branches'][0]['is_package'])
            self.assertEqual(obj['branches'][0]['entries'][0].metadata['id'],self.simple.pk)

    def test_resource_total_counts_shared_assignment_once_and_is_above_details(self):
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.equipment,count=3)
        row=fixtures.WorkResourceTests.assignment(self,self.version,count=2,end_date=date(2026,1,31),shifts_per_day=1,include_crew=False)
        from .resource_targets import replace
        replace(row,[(self.composite.pk,self.a.pk),(self.composite.pk,self.b.pk)])
        response=self.client.get(self.gpr)
        section=next(s for s in response.context['plan_sections'] if s['kind']=='equipment')
        self.assertEqual(section['totals'][0]['after'],Decimal(5))
        entry=next(e for group in section['groups'] for e in group['entries'] if e.native_prefix)
        html=response.content.decode()
        self.assertLess(html.index('data-plan-section-totals="equipment"'),html.index('data-entry-id="'+entry.pk+'"'))
        self.assertNotIn('Итоги раздела',html)


from . import test_global_matrix as report_fixtures

class ResourceHeaderTotalTests(TestCase):
    setUpTestData=classmethod(report_fixtures.GlobalMatrixTests.setUpTestData.__func__)
    create=report_fixtures.GlobalMatrixTests.create
    approve=report_fixtures.GlobalMatrixTests.approve
    setUp=report_fixtures.GlobalMatrixTests.setUp
    def test_period_totals_use_existing_values_without_separate_fold_and_position_labels(self):
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=self.equipment,count=3)
        for url,params in [(self.url,{}),(reverse('planning:journal_plan_list'),{'start':'2026-01-01','end':'2026-01-31','sections':['labor','equipment']})]:
            response=self.client.get(url,params);self.assertEqual(response.status_code,200)
            html=response.content.decode()
            for kind in ['labor','equipment']:
                self.assertIn('data-matrix-section="'+kind+'"',html)
                self.assertIn('class="report-calendar-summary"',html)
                self.assertNotIn('data-resource-section-total="'+kind+'"',html)
                self.assertNotIn('<summary>Итого: '+('Должность' if kind=='labor' else 'Техника'),html)
                section=next(s for s in response.context['objects'][0]['sections'] if s['kind']==kind)
                self.assertEqual(section['total_node']['rows'],section['subtotals'])
            self.assertNotIn('Название бригады',html);self.assertNotIn('Группа бригад',html)
            self.assertIn('Группа должностей',html)
