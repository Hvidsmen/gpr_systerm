from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import Brigade, BrigadeMerge, EquipmentType, EquipmentCrewPosition
from apps.resources.position_catalog import brigade_for
from . import test_work_resources as fixtures
from .models import ResourceMonthAllocation
from .plan_workbench import add_month_resource
from .daily_schedule_table import add_entry, save_table
from .daily_schedule_table_forms import AddEntryForm
from .independent_smg import create


class TableEquipmentCrewTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.schedule=create(self.planner,self.obj,fixtures.JAN)
        self.data={'kind':'equipment','equipment':self.equipment,'rate':20}
        self.positions={brigade_for(self.position),brigade_for(self.helper)}

    def test_new_machine_includes_crew_positions_in_gpr_and_smg_tables(self):
        add_month_resource(self.planner,self.version,fixtures.JAN,self.data)
        add_entry(self.planner,self.schedule,self.data)
        self.assertEqual(set(self.version.resource_allocations.filter(kind='labor').values_list('brigade_id',flat=True)),self.positions)
        self.assertEqual(self.schedule.entries.filter(kind='labor').count(),2)
        self.assertTrue(all(row.count==0 for row in self.version.resource_allocations.all()))
        self.assertTrue(all(Decimal(row['planned_count'])==0 for entry in self.schedule.entries.all() for row in entry.rows))
        for url in [reverse('planning:workspace_edit',args=[self.version.pk]),reverse('planning:daily_schedule_detail',args=[self.schedule.pk])]:
            response=self.client.get(url)
            self.assertContains(response,'Машинист');self.assertContains(response,'Помощник')

    def test_existing_people_values_and_rates_are_preserved_on_add_and_repeat(self):
        brigade=Brigade.objects.get(pk=brigade_for(self.position))
        monthly=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='labor',brigade=brigade,count=7,hours=160,rate=88)
        people=add_entry(self.planner,self.schedule,{'kind':'labor','brigade':brigade,'rate':99})
        save_table(self.planner,self.schedule,[{'id':people.pk,'revision':people.revision,'changes':[{'date':fixtures.JAN.isoformat(),'item_id':None,'field':'planned_count','value':'4'}]}])
        people.refresh_from_db();before=people.rows
        for _ in range(2):add_month_resource(self.planner,self.version,fixtures.JAN,self.data)
        add_entry(self.planner,self.schedule,self.data)
        monthly.refresh_from_db();people.refresh_from_db()
        self.assertEqual((monthly.count,monthly.hours,monthly.rate),(7,160,88))
        self.assertEqual(people.rows,before)
        self.assertEqual(self.version.resource_allocations.filter(kind='labor').count(),2)
        self.assertEqual(self.schedule.entries.filter(kind='labor').count(),2)

    def test_readding_existing_machine_repairs_missing_crew_without_replacing_machine(self):
        machine=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='equipment',equipment_type=self.equipment,count=3,hours=120,rate=60)
        self.assertTrue(add_month_resource(self.planner,self.version,fixtures.JAN,self.data))
        machine.refresh_from_db();self.assertEqual((machine.count,machine.hours,machine.rate),(3,120,60))
        entry=add_entry(self.planner,self.schedule,self.data)
        self.schedule.entries.filter(kind='labor').delete()
        stored=entry.rows
        repaired=add_entry(self.planner,self.schedule,self.data)
        self.assertEqual(repaired.pk,entry.pk);entry.refresh_from_db();self.assertEqual(entry.rows,stored)
        self.assertEqual(self.schedule.entries.filter(kind='labor').count(),2)

    def test_two_equipment_types_share_position_rows_and_no_crew_is_valid(self):
        second=EquipmentType.objects.create(company=self.company,name='Второй экскаватор')
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=second,position=self.position,count=2)
        plain=EquipmentType.objects.create(company=self.company,name='Без экипажа')
        for gear in [self.equipment,second,plain]:
            data={**self.data,'equipment':gear}
            add_month_resource(self.planner,self.version,fixtures.JAN,data)
            add_entry(self.planner,self.schedule,data)
        self.assertEqual(self.version.resource_allocations.filter(kind='equipment').count(),3)
        self.assertEqual(self.version.resource_allocations.filter(kind='labor').count(),2)
        self.assertEqual(self.schedule.entries.filter(kind='labor').count(),2)

    def test_failed_crew_addition_rolls_back_equipment_and_labor_together(self):
        with patch('apps.planning.table_resource_crew.crew_positions',side_effect=RuntimeError('crew failure')):
            with self.assertRaises(RuntimeError):add_month_resource(self.planner,self.version,fixtures.JAN,self.data)
            with self.assertRaises(RuntimeError):add_entry(self.planner,self.schedule,self.data)
        self.assertFalse(self.version.resource_allocations.exists())
        self.assertFalse(self.schedule.entries.exists())

    def test_search_excludes_merged_and_inactive_positions_in_both_editors(self):
        target=Brigade.objects.create(company=self.company,name='Старший производитель работ')
        old=Brigade.objects.create(company=self.company,name='Производитель работ')
        inactive=Brigade.objects.create(company=self.company,name='Производитель работ неактивный',is_active=False)
        BrigadeMerge.objects.create(company=self.company,source=old,target=target,created_by=self.planner,audit={'test':True})
        for owner in [self.version,self.schedule]:
            form=AddEntryForm(schedule=owner)
            choices=set(form.fields['brigade'].queryset.values_list('pk',flat=True))
            self.assertIn(target.pk,choices);self.assertNotIn(old.pk,choices);self.assertNotIn(inactive.pk,choices)
            bound=AddEntryForm({'kind':'labor','brigade':old.pk},schedule=owner)
            self.assertFalse(bound.is_valid());self.assertIn('brigade',bound.errors)
        from .bulk_add import BulkAddForm
        form=BulkAddForm(version=self.version,kind='labor',company=self.company)
        self.assertNotIn(old,form.fields['items'].queryset)
