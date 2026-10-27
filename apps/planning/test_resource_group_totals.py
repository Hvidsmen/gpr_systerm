from decimal import Decimal
from types import SimpleNamespace
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import BrigadeGroup,EquipmentType,EquipmentCategory,Brigade
from apps.resources.position_catalog import brigade_for
from . import test_work_resources as fixtures,resource_targets
from .models import ResourceMonthAllocation,WorkMonthAllocation
from .independent_smg import create
from .resource_group_totals import total


class ResourceGroupTotalTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner
    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.group=BrigadeGroup.objects.create(company=self.company,name='Водители вспомогательной техники')
        self.driver=Brigade.objects.get(pk=brigade_for(self.position))
        self.helper_brigade=Brigade.objects.get(pk=brigade_for(self.helper))
        for row in [self.driver,self.helper_brigade]:row.group=self.group;row.save()

    def test_gpr_group_sum_includes_manual_and_shared_assignment_once(self):
        category=EquipmentCategory.objects.create(company=self.company,name='Землеройная техника')
        self.equipment.category=category;self.equipment.save()
        for brigade,count in [(self.driver,2),(self.helper_brigade,3)]:
            ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='labor',brigade=brigade,count=count)
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=self.equipment,count=2)
        child=EquipmentType.objects.create(company=self.company,name='JCB 3CX',parent=self.equipment)
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=child,count=3)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=self.composite,quantity=1)
        assignment=fixtures.WorkResourceTests.assignment(self,self.version,count=4,include_crew=False,end_date=fixtures.JAN.replace(day=31),shifts_per_day=1)
        resource_targets.replace(assignment,[(self.composite.pk,self.a.pk),(self.composite.pk,self.b.pk)])
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        labor=response.context['plan_sections'][1];equipment=response.context['plan_sections'][2]
        self.assertEqual(labor['groups'][0]['totals'][0]['after'],5)
        self.assertEqual(len(equipment['groups']),1)
        self.assertEqual(equipment['groups'][0]['label'],category.name)
        self.assertEqual(equipment['groups'][0]['totals'][0]['after'],9)
        self.assertEqual(equipment['totals'][0]['after'],9)
        self.assertContains(response,'data-plan-group-total-number')
        body=response.content.decode().split('data-plan-group-totals="labor"',1)[1].split('</tbody>',1)[0]
        self.assertNotIn('<input',body)

    def test_smg_group_daily_totals_and_month_average(self):
        schedule=create(self.planner,self.obj,fixtures.JAN)
        for index,brigade in enumerate([self.driver,self.helper_brigade]):
            rows=[{'date':'2026-01-01','brigade_id':brigade.pk,'planned_workers':str(2+index),'planned_count':str(2+index),'planned_hours':'0'},
                  {'date':'2026-01-02','brigade_id':brigade.pk,'planned_workers':str(4+2*index),'planned_count':str(4+2*index),'planned_hours':'0'}]
            schedule.entries.create(key='labor:group:'+str(brigade.pk),kind='labor',label=brigade.name,rows=rows,original=rows)
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk]))
        group=response.context['plan_sections'][1]['groups'][0]
        self.assertEqual(group['label'],self.group.name)
        result=group['totals'][0]
        self.assertEqual([cell['after'] for cell in result['cells'][:2]],[5,10])
        self.assertEqual(result['after'],Decimal(15)/31)
        self.assertEqual(result['cells'][0]['date'],'2026-01-01')
        self.assertContains(response,'data-plan-total-date="2026-01-01"')
        self.assertContains(response,'Среднее за месяц')

    def test_equipment_types_group_by_category_and_uncategorized_together(self):
        other=EquipmentType.objects.create(company=self.company,name=self.equipment.name)
        for equipment,count in [(self.equipment,2),(other,7)]:
            ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=equipment,count=count)
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        groups=response.context['plan_sections'][2]['groups']
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['label'],'Без категории')
        self.assertEqual(groups[0]['totals'][0]['after'],9)
        self.assertEqual(response.context['plan_sections'][2]['totals'][0]['after'],9)

    def test_smg_equipment_categories_combine_types_and_preserve_separate_categories(self):
        from .daily_schedule_table import add_entry,save_table
        category=EquipmentCategory.objects.create(company=self.company,name='Землеройная техника')
        second_category=EquipmentCategory.objects.create(company=self.company,name='Подъёмная техника')
        self.equipment.category=category;self.equipment.save()
        other=EquipmentType.objects.create(company=self.company,name='Другая машина',category=category)
        crane=EquipmentType.objects.create(company=self.company,name='Кран',category=second_category)
        schedule=create(self.planner,self.obj,fixtures.JAN)
        for equipment,count in [(self.equipment,2),(other,3),(crane,7)]:
            entry=add_entry(self.planner,schedule,{'kind':'equipment','equipment':equipment,'rate':0})
            save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':str(count)}]}])
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk]))
        groups={g['label']:g for g in response.context['plan_sections'][2]['groups']}
        self.assertEqual(groups[category.name]['totals'][0]['cells'][0]['after'],5)
        self.assertEqual(groups[second_category.name]['totals'][0]['cells'][0]['after'],7)

    def test_daily_reference_and_deviation_totals_use_daily_counts(self):
        entries=[SimpleNamespace(comparison=[{'before':99,'after':99,'cells':[{'date':'2026-01-01','before':1,'after':3},{'date':'2026-01-02','before':2,'after':4}]}]),
                 SimpleNamespace(comparison=[{'before':99,'after':99,'cells':[{'date':'2026-01-01','before':2,'after':5},{'date':'2026-01-02','before':4,'after':8}]}])]
        result=total(entries,'labor')
        self.assertEqual([c['after'] for c in result['cells']],[8,12])
        self.assertEqual((result['before'],result['after'],result['delta']),(Decimal('4.5'),10,Decimal('5.5')))
        self.assertEqual([c['delta'] for c in result['cells']],[5,6])
