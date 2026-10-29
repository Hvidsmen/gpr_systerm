from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import BrigadeGroup,EquipmentCategory,Brigade,EquipmentType
from . import test_resource_group_totals as fixtures
from .models import ResourceMonthAllocation
from .independent_smg import create
from .daily_schedule_table import add_entry,save_table


class ConfiguredPlanGroupTests(TestCase):
    setUpTestData=classmethod(fixtures.ResourceGroupTotalTests.setUpTestData.__func__)
    setUp=fixtures.ResourceGroupTotalTests.setUp
    prepare_owner=fixtures.ResourceGroupTotalTests.prepare_owner

    def setup_resources(self):
        a=BrigadeGroup.objects.create(company=self.company,name='А должности',sort_order=90)
        z=BrigadeGroup.objects.create(company=self.company,name='Я должности',sort_order=0)
        self.driver.group=a;self.driver.save();self.helper_brigade.group=z;self.helper_brigade.save()
        c=EquipmentCategory.objects.create(company=self.company,name='А машины',sort_order=90)
        d=EquipmentCategory.objects.create(company=self.company,name='Я машины',sort_order=0)
        self.equipment.category=c;self.equipment.save()
        second=EquipmentType.objects.create(company=self.company,name='Вторая машина',category=d)
        return [(self.driver,2),(self.helper_brigade,3)],[(self.equipment,4),(second,0)]

    def test_single_and_all_month_gpr_group_order_and_visible_zero_totals(self):
        labor,equipment=self.setup_resources()
        for kind,items in [('labor',labor),('equipment',equipment)]:
            for item,count in items:
                ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.fixtures.JAN,
                    kind=kind,count=count,**({'brigade':item} if kind=='labor' else {'equipment_type':item}))
        page=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        for kind,expected in [('labor',[3,2]),('equipment',[0,4])]:
            section=next(s for s in page.context['plan_sections'] if s['kind']==kind)
            self.assertEqual([g['totals'][0]['after'] for g in section['groups']],expected)
            self.assertContains(page,f'data-plan-group-totals="{kind}"',count=2)
        page=self.client.get(reverse('planning:workspace_months',args=[self.version.pk]))
        self.assertEqual(page.status_code,200)
        for kind,expected in [('labor',[3,2]),('equipment',[0,4])]:
            section=next(s for s in page.context['sections'] if s['kind']==kind)
            self.assertEqual([g['totals'][0]['value'] for g in section['groups']],expected)
        self.assertContains(page,'data-grid-resource-group',count=4)
        self.assertContains(page,'data-grid-total-month="2026-01-01"',count=4)

    def test_smg_daily_group_totals_and_configured_order(self):
        labor,equipment=self.setup_resources();schedule=create(self.planner,self.obj,fixtures.fixtures.JAN)
        for kind,items in [('labor',labor),('equipment',equipment)]:
            for item,count in items:
                entry=add_entry(self.planner,schedule,{'kind':kind,'brigade' if kind=='labor' else 'equipment':item,'rate':0,'add_crew':False})
                save_table(self.planner,schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':str(count)}]}])
        page=self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk]))
        for kind,expected in [('labor',[3,2]),('equipment',[0,4])]:
            section=next(s for s in page.context['plan_sections'] if s['kind']==kind)
            self.assertEqual([g['totals'][0]['cells'][0]['after'] for g in section['groups']],expected)
            self.assertContains(page,f'data-plan-group-totals="{kind}"',count=2)
        self.assertContains(page,'Итого · чел.')
        self.assertContains(page,'Итого · ед.')

    def test_all_month_assignments_share_real_groups_and_round_only_final_sum(self):
        from . import test_work_resources as resources
        self.setup_resources()
        for _ in range(2):resources.WorkResourceTests.assignment(self,self.version,count=1,include_crew=False,
            end_date=resources.JAN,shifts_per_day=1)
        page=self.client.get(reverse('planning:workspace_months',args=[self.version.pk]))
        section=next(s for s in page.context['sections'] if s['kind']=='equipment')
        self.assertEqual(len(section['groups']),1)
        group=section['groups'][0];self.assertEqual(group['label'],'А машины')
        self.assertEqual(group['totals'][0]['value'],Decimal(2)/31)
        self.assertEqual(len(group['entries']),2)
