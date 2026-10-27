import json
from datetime import date
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import EquipmentType, EquipmentCrewPosition
from . import test_table_crew_counts as count_tests
from . import test_work_resources as fixtures
from .plan_workbench import add_month_resource, remove_smg_entry
from .table_resource_crew import sync_monthly, sync_daily
from .daily_schedule_table import add_entry, save_table


class CrewRemovalTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner = fixtures.WorkResourceTests.prepare_owner
    setUp = count_tests.TableCrewCountTests.setUp
    monthly = count_tests.TableCrewCountTests.monthly
    daily = count_tests.TableCrewCountTests.daily
    edit = count_tests.TableCrewCountTests.edit

    def choice(self, mode, machines, daily=False):
        return {str(self.brigade.pk): {'mode': mode, 'equipment': {str(g.pk): str(g.revision) if daily else '' for g in machines}}}

    def monthly_post(self, person, actions=None):
        removed_ids={p.pk for p in person} if isinstance(person,list) else {person.pk}
        url = reverse('planning:workspace_edit', args=[self.version.pk]) + '?month=2026-01-01'
        response = self.client.get(url)
        data = {'action': 'save'}
        for key in ['work_forms', 'resource_forms']:
            formset = response.context[key]
            for name in formset.management_form.fields:
                data[formset.management_form.add_prefix(name)] = formset.management_form[name].value()
            for form in formset:
                for name in form.fields:
                    value = form[name].value()
                    if value is not None and value is not False:
                        data[form.add_prefix(name)] = 'on' if value is True else value
                if key == 'resource_forms' and form.instance.pk in removed_ids:
                    for name in form.fields:
                        if name not in {'id','DELETE'}:
                            data.pop(form.add_prefix(name),None)
                    data[form.add_prefix('DELETE')] = 'on'
        if actions is not None:
            data['crew_removal_actions'] = json.dumps(actions)
        return self.client.post(url, data)

    def test_gpr_detach_keeps_machine_and_does_not_restore_position(self):
        gear, people = self.monthly()
        gear.count = 2; gear.save(); sync_monthly(self.planner, self.version, fixtures.JAN)
        response = self.monthly_post(people, self.choice('detach', [gear]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.version.resource_allocations.filter(pk=people.pk).exists())
        gear.refresh_from_db(); self.assertEqual(gear.count, 2)
        gear.count = 5; gear.save(); sync_monthly(self.planner, self.version, fixtures.JAN)
        self.assertFalse(self.version.resource_allocations.filter(kind='labor', brigade=self.brigade).exists())
        helper = self.version.resource_allocations.get(kind='labor')
        self.assertEqual(helper.count, 5)
        self.assertTrue(self.equipment.crew_positions.filter(position=self.position).exists())

    def test_gpr_cascade_removes_all_linked_machines_and_preserves_extra_people(self):
        gear, people = self.monthly()
        other = EquipmentType.objects.create(company=self.company, name='Второй вид')
        EquipmentCrewPosition.objects.create(company=self.company, equipment_type=other, position=self.position, count=1)
        add_month_resource(self.planner, self.version, fixtures.JAN, {**self.data, 'equipment': other})
        second = self.version.resource_allocations.get(kind='equipment', equipment_type=other)
        gear.count = 2; gear.save(); sync_monthly(self.planner, self.version, fixtures.JAN)
        helper = self.version.resource_allocations.exclude(brigade=self.brigade).get(kind='labor')
        helper.count = 5; helper.save()
        response = self.monthly_post(people, self.choice('remove_equipment', [gear, second]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.version.resource_allocations.filter(kind='equipment').exists())
        self.assertFalse(self.version.resource_allocations.filter(pk=people.pk).exists())
        helper.refresh_from_db(); self.assertEqual(helper.count, 3)
        self.assertTrue(EquipmentType.objects.filter(pk=other.pk).exists())

    def test_gpr_requires_choice_even_when_machine_count_is_zero(self):
        gear, people = self.monthly()
        self.monthly_post(people)
        self.assertTrue(self.version.resource_allocations.filter(pk=people.pk).exists())
        self.assertTrue(self.version.resource_allocations.filter(pk=gear.pk).exists())

    def test_gpr_multiple_decisions_use_click_order_instead_of_numeric_role_ids(self):
        gear,people=self.monthly()
        helper=self.version.resource_allocations.exclude(pk=people.pk).get(kind='labor')
        actions=self.choice('remove_equipment',[gear])
        actions[str(self.brigade.pk)]['order']=1
        actions[str(helper.brigade_id)]={'mode':'detach','equipment':{str(gear.pk):''},'order':0}
        response=self.monthly_post([people,helper],actions)
        self.assertEqual(response.status_code,302)
        self.assertFalse(self.version.resource_allocations.exists())

    def test_smg_detach_persists_and_preserves_other_crew(self):
        gear, people = self.daily()
        save_table(self.planner, self.schedule, [self.edit(gear, '2026-01-01', 2)])
        gear.refresh_from_db(); people.refresh_from_db()
        response = self.client.post(reverse('planning:daily_schedule_detail', args=[self.schedule.pk]), {
            'action': 'remove_entry', 'entry_id': people.pk, 'revision': people.revision,
            'crew_removal_actions': json.dumps(self.choice('detach', [gear], True)),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.schedule.entries.filter(pk=people.pk).exists())
        save_table(self.planner, self.schedule, [self.edit(gear, '2026-01-01', 4)])
        self.assertFalse(self.schedule.entries.filter(kind='labor', label=people.label).exists())
        helper = self.schedule.entries.get(kind='labor')
        self.assertEqual(helper.rows[0]['planned_workers'], '4')
        self.assertTrue(self.equipment.crew_positions.filter(position=self.position).exists())

    def test_smg_cascade_removes_all_linked_machines_preserves_other_extras(self):
        gear, people = self.daily()
        save_table(self.planner, self.schedule, [self.edit(gear, '2026-01-01', 2)])
        helper = self.schedule.entries.exclude(pk=people.pk).get(kind='labor')
        save_table(self.planner, self.schedule, [self.edit(helper, '2026-01-01', 5)])
        other = EquipmentType.objects.create(company=self.company, name='Другая техника')
        EquipmentCrewPosition.objects.create(company=self.company, equipment_type=other, position=self.position, count=1)
        second = add_entry(self.planner, self.schedule, {**self.data, 'equipment': other})
        people.refresh_from_db(); gear.refresh_from_db()
        remove_smg_entry(self.planner, self.schedule, people.pk, people.revision,
                         removal_actions=self.choice('remove_equipment', [gear, second], True))
        self.assertFalse(self.schedule.entries.filter(kind='equipment').exists())
        self.assertFalse(self.schedule.entries.filter(pk=people.pk).exists())
        helper.refresh_from_db(); self.assertEqual(helper.rows[0]['planned_workers'], '3')

    def test_stale_machine_revision_rolls_back_everything(self):
        gear, people = self.daily()
        actions = self.choice('remove_equipment', [gear], True)
        save_table(self.planner, self.schedule, [self.edit(gear, '2026-01-01', 1)])
        people.refresh_from_db()
        with self.assertRaises(ValidationError):
            remove_smg_entry(self.planner, self.schedule, people.pk, people.revision, removal_actions=actions)
        self.assertTrue(self.schedule.entries.filter(pk=gear.pk).exists())
        self.assertTrue(self.schedule.entries.filter(pk=people.pk).exists())

    def test_no_choice_or_forged_machine_set_is_rejected(self):
        gear, people = self.daily()
        for actions in [None, self.choice('detach', [], True), {'bad': 'request'}]:
            with self.subTest(actions=actions), self.assertRaises(ValidationError):
                remove_smg_entry(self.planner, self.schedule, people.pk, people.revision, removal_actions=actions)
        self.assertTrue(self.schedule.entries.filter(pk=people.pk).exists())

    def test_closed_days_and_permissions_still_protect_cascade(self):
        gear, people = self.daily()
        actions = self.choice('remove_equipment', [gear], True)
        self.schedule.locked_before = date(2026, 1, 2); self.schedule.save()
        with self.assertRaises(ValidationError):
            remove_smg_entry(self.planner, self.schedule, people.pk, people.revision, removal_actions=actions)
        self.schedule.locked_before = None; self.schedule.save()
        with self.assertRaises(PermissionDenied):
            remove_smg_entry(self.resource_estimator, self.schedule, people.pk, people.revision, removal_actions=actions)
        self.assertEqual(self.schedule.entries.filter(kind='equipment').count(), 1)
