from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from . import test_table_resource_crew as fixtures
from .plan_workbench import add_month_resource
from .table_resource_crew import sync_monthly
from .daily_schedule_table import add_entry,save_table
from .daily_schedule_table_forms import AddEntryForm
from .workspace_services import build_workspace_snapshot


class TableCrewShiftTests(TestCase):
    setUpTestData=classmethod(fixtures.TableEquipmentCrewTests.setUpTestData.__func__)
    setUp=fixtures.TableEquipmentCrewTests.setUp
    prepare_owner=fixtures.TableEquipmentCrewTests.prepare_owner

    def test_monthly_two_shifts_multiply_crews_preserve_extras_and_snapshot(self):
        data={**self.data,'shifts_per_day':2}
        add_month_resource(self.planner,self.version,fixtures.fixtures.JAN,data)
        machine=self.version.resource_allocations.get(kind='equipment');machine.count=3;machine.save()
        sync_monthly(self.planner,self.version,fixtures.fixtures.JAN)
        self.assertEqual(set(self.version.resource_allocations.filter(kind='labor').values_list('count',flat=True)),{6})
        person=self.version.resource_allocations.filter(kind='labor').first();person.count=8;person.save()
        machine.count=4;machine.save();sync_monthly(self.planner,self.version,fixtures.fixtures.JAN)
        person.refresh_from_db();self.assertEqual(person.count,10)
        machine.refresh_from_db();self.assertEqual(machine.crew_data['shifts_per_day'],2)
        snapshot=build_workspace_snapshot(self.version)
        self.assertTrue(all(row['shifts_per_day']==2 for row in snapshot['resources']['equipment']))

    def test_daily_two_shifts_use_daily_machine_count_and_effective_ui_recipe(self):
        entry=add_entry(self.planner,self.schedule,{**self.data,'shifts_per_day':2})
        save_table(self.planner,self.schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-01-01','item_id':None,'field':'planned_count','value':'3'}]}])
        for person in self.schedule.entries.filter(kind='labor'):
            self.assertEqual(Decimal(person.rows[0]['planned_count']),6)
            self.assertEqual(Decimal(person.rows[1]['planned_count']),0)
        entry.refresh_from_db();self.assertEqual(entry.metadata['shifts_per_day'],2)
        from django.urls import reverse
        self.assertContains(self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk])),'Смен в день: 2')

    def test_shift_default_and_bounds_and_crew_checkbox(self):
        self.assertEqual(AddEntryForm(schedule=self.version).fields['shifts_per_day'].initial,1)
        for count in [0,-1,25,'1.5']:
            form=AddEntryForm({'kind':'equipment','equipment':self.equipment.pk,'shifts_per_day':count},schedule=self.version,user=self.planner)
            self.assertFalse(form.is_valid());self.assertIn('shifts_per_day',form.errors)
        add_month_resource(self.planner,self.version,fixtures.fixtures.JAN,{**self.data,'shifts_per_day':2,'add_crew':False})
        self.assertFalse(self.version.resource_allocations.filter(kind='labor').exists())
