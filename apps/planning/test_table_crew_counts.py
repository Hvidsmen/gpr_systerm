from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import Brigade, BrigadeGroup, BrigadeMacroGroup, EquipmentType, EquipmentCrewPosition
from apps.resources.position_catalog import brigade_for
from . import test_work_resources as fixtures
from .models import ResourceMonthAllocation
from .plan_workbench import add_month_resource,remove_smg_entry
from .daily_schedule_table import add_entry,save_table
from .table_resource_crew import sync_monthly,sync_daily
from .independent_smg import create,revise
from .workspace_services import build_workspace_snapshot


class TableCrewCountTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner
    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.schedule=create(self.planner,self.obj,fixtures.JAN)
        self.brigade=Brigade.objects.get(pk=brigade_for(self.position))
        self.data={'kind':'equipment','equipment':self.equipment,'rate':0}

    def monthly(self):
        add_month_resource(self.planner,self.version,fixtures.JAN,self.data)
        return self.version.resource_allocations.get(kind='equipment'),self.version.resource_allocations.get(kind='labor',brigade=self.brigade)

    def test_monthly_crew_increases_decreases_and_preserves_additional_people(self):
        gear,people=self.monthly()
        gear.count=1;gear.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        people.refresh_from_db();self.assertEqual(people.count,1)
        people.count=4;people.save();sync_monthly(self.planner,self.version,fixtures.JAN,edited_counts={people.pk:4})
        gear.count=2;gear.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        people.refresh_from_db();self.assertEqual(people.count,5)
        gear.count=0;gear.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        people.refresh_from_db();self.assertEqual(people.count,3)
        gear.delete();sync_monthly(self.planner,self.version,fixtures.JAN)
        people.refresh_from_db();self.assertEqual(people.count,3)

    def test_monthly_two_machine_types_use_captured_ratios_and_count_once_in_snapshot(self):
        gear,people=self.monthly()
        other=EquipmentType.objects.create(company=self.company,name='Другой экскаватор')
        EquipmentCrewPosition.objects.create(company=self.company,equipment_type=other,position=self.position,count=2)
        add_month_resource(self.planner,self.version,fixtures.JAN,{**self.data,'equipment':other})
        second=self.version.resource_allocations.get(kind='equipment',equipment_type=other)
        gear.count=2;gear.save();second.count=3;second.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        people.refresh_from_db();self.assertEqual(people.count,8)
        EquipmentCrewPosition.objects.filter(equipment_type=other).update(count=9)
        sync_monthly(self.planner,self.version,fixtures.JAN);people.refresh_from_db();self.assertEqual(people.count,8)
        snapshot=build_workspace_snapshot(self.version)
        values=[r for r in snapshot['resources']['labor'] if r['brigade_id']==self.brigade.pk]
        self.assertEqual(sum(Decimal(str(r['planned_workers'])) for r in values),8*31)
        self.assertTrue(all(r['table_crew_count']==8 for r in values))

    def daily(self):
        gear=add_entry(self.planner,self.schedule,self.data)
        people=next(e for e in self.schedule.entries.filter(kind='labor') if e.rows[0]['brigade_id']==self.brigade.pk)
        return gear,people

    def edit(self,entry,day,value):
        entry.refresh_from_db()
        return {'id':entry.pk,'revision':entry.revision,'changes':[{'date':day,'item_id':None,'field':'planned_count','value':str(value)}]}

    def test_daily_counts_follow_each_day_and_leave_other_days_and_extra_people(self):
        gear,people=self.daily()
        save_table(self.planner,self.schedule,[self.edit(gear,'2026-01-01',1)])
        people.refresh_from_db();self.assertEqual(people.rows[0]['planned_workers'],'1');self.assertEqual(people.rows[1]['planned_workers'],'0')
        save_table(self.planner,self.schedule,[self.edit(people,'2026-01-01',4)])
        save_table(self.planner,self.schedule,[self.edit(gear,'2026-01-01',2)])
        people.refresh_from_db();self.assertEqual(people.rows[0]['planned_workers'],'5')
        save_table(self.planner,self.schedule,[self.edit(gear,'2026-01-01',0)])
        people.refresh_from_db();self.assertEqual(people.rows[0]['planned_workers'],'3')

    def test_daily_batch_order_independent_and_invalid_total_rolls_back(self):
        gear,people=self.daily()
        save_table(self.planner,self.schedule,[self.edit(people,'2026-01-02',5),self.edit(gear,'2026-01-02',2)])
        people.refresh_from_db();self.assertEqual(people.rows[1]['planned_workers'],'5')
        with self.assertRaises(ValidationError):
            save_table(self.planner,self.schedule,[self.edit(gear,'2026-01-02',8),self.edit(people,'2026-01-02',1)])
        gear.refresh_from_db();self.assertEqual(gear.rows[1]['planned_count'],'2')
        people.refresh_from_db();self.assertEqual(people.rows[1]['planned_workers'],'5')

    def test_closed_daily_history_and_machine_removal(self):
        gear,people=self.daily()
        save_table(self.planner,self.schedule,[{'id':gear.pk,'revision':gear.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':'1'},{'date':'2026-01-02','item_id':None,'field':'planned_count','value':'2'}]}])
        people.refresh_from_db();past=dict(people.rows[0])
        self.schedule.locked_before=date(2026,1,2);self.schedule.save()
        save_table(self.planner,self.schedule,[self.edit(gear,'2026-01-02',3)])
        people.refresh_from_db();self.assertEqual(people.rows[0],past);self.assertEqual(people.rows[1]['planned_workers'],'3')

    def test_positions_group_only_without_macrogroup_in_both_editors(self):
        group=BrigadeGroup.objects.create(company=self.company,name='Машинисты')
        macro=BrigadeMacroGroup.objects.create(company=self.company,name='Не показывать макрогруппу')
        self.brigade.group=group;self.brigade.macro_group=macro;self.brigade.save()
        self.monthly();self.daily()
        for url in [reverse('planning:workspace_edit',args=[self.version.pk]),reverse('planning:daily_schedule_detail',args=[self.schedule.pk])]:
            response=self.client.get(url)
            labels=[g['label'] for g in response.context['plan_sections'][1]['groups']]
            self.assertIn('Машинисты',labels);self.assertIn('Без группы',labels)
            self.assertNotContains(response,'Не показывать макрогруппу')

    def test_gpr_to_smg_carries_crew_baseline_without_counting_it_twice(self):
        from .models import GlobalPlanVersion
        from .daily_schedule import create as from_gpr
        gear,people=self.monthly()
        gear.count=2;gear.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        people.refresh_from_db();people.count=5;people.save()
        sync_monthly(self.planner,self.version,fixtures.JAN,edited_counts={people.pk:5})
        snapshot=build_workspace_snapshot(self.version)
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='APPROVED',snapshot=snapshot)
        self.version.refresh_from_db();self.schedule.delete()
        schedule=from_gpr(self.planner,self.version,fixtures.JAN)
        machine=schedule.entries.get(kind='equipment')
        person=next(e for e in schedule.entries.filter(kind='labor') if e.rows[0]['brigade_id']==self.brigade.pk)
        sync_daily(self.planner,schedule)
        person.refresh_from_db();self.assertEqual(Decimal(person.rows[0]['planned_workers']),5)
        save_table(self.planner,schedule,[self.edit(machine,'2026-01-01',3)])
        person.refresh_from_db();self.assertEqual(Decimal(person.rows[0]['planned_workers']),6)
        self.version.refresh_from_db();self.assertEqual(self.version.snapshot,snapshot)

    def test_merged_position_retains_crew_baseline_and_uses_canonical_role(self):
        from apps.resources.brigade_merge import merge_brigades
        gear,people=self.monthly();gear.count=1;gear.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        target=Brigade.objects.create(company=self.company,name='Машинист экскаватора')
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,kind='labor',brigade=target,count=0,rate=100)
        merge_brigades(self.planner,target.pk,[self.brigade.pk])
        gear.count=2;gear.save();sync_monthly(self.planner,self.version,fixtures.JAN)
        self.assertEqual(self.version.resource_allocations.get(kind='labor',brigade=target).count,2)
        self.assertFalse(self.version.resource_allocations.filter(kind='labor',brigade=self.brigade).exists())
