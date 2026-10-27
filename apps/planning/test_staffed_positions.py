from datetime import date

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Company
from apps.resources.models import Brigade, BrigadeMerge, Employee
from apps.resources.position_catalog import available_positions, planning_brigades, planning_positions, position_for
from . import test_work_resources as fixtures, work_resources
from .bulk_add import BulkAddForm
from .daily_schedule_table_forms import AddEntryForm
from .graph_crew import editor_data
from .independent_smg import create
from .models import ResourceMonthAllocation, WorkResourceAssignment
from .resource_editor import InlineAssignmentForm
from .workspace_forms import AddPeriodResourceForm, ResourceAllocationSet


class StaffedPlanningPositionsTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner = fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.schedule = create(self.planner, self.obj, fixtures.JAN)
        self.prepare_owner(self.schedule)
        self.staffed = Brigade.objects.create(company=self.company, name='Есть активные сотрудники')
        self.archived = Brigade.objects.create(company=self.company, name='Только архивные сотрудники')
        self.empty = Brigade.objects.create(company=self.company, name='Без сотрудников')
        self.inactive = Brigade.objects.create(company=self.company, name='Неактивная должность', is_active=False)
        self.employee(self.staffed)
        self.employee(self.staffed)
        self.employee(self.staffed, active=False)
        self.employee(self.archived, active=False)
        self.employee(self.inactive)

    def employee(self, brigade, active=True, company=None):
        return Employee.objects.create(company=company or self.company, position=position_for(brigade),
            first_name='Иван', last_name='Иванов', is_active=active)

    def data(self, position):
        return {'resource_kind': 'labor', 'target_scope': 'object', 'position': position.pk,
            'count': 2, 'shifts_per_day': 1, 'hours_per_shift': 8,
            'start_date': fixtures.JAN.isoformat(), 'end_date': '2026-01-02'}

    def test_all_new_selectors_require_active_employees_in_both_plans(self):
        for owner in [self.version, self.schedule]:
            form = AddEntryForm(schedule=owner)
            choices = list(form.fields['brigade'].queryset.values_list('pk', flat=True))
            self.assertEqual(choices.count(self.staffed.pk), 1)
            for row in [self.archived, self.empty, self.inactive]:
                self.assertNotIn(row.pk, choices)
                self.assertFalse(AddEntryForm({'kind': 'labor', 'brigade': row.pk}, schedule=owner).is_valid())
            self.assertTrue(AddEntryForm({'kind': 'labor', 'brigade': self.staffed.pk}, schedule=owner).is_valid())
            positions = InlineAssignmentForm(owner=owner).fields['position'].queryset
            self.assertIn(position_for(self.staffed), positions)
            self.assertNotIn(position_for(self.archived), positions)
            self.assertFalse(InlineAssignmentForm(self.data(position_for(self.empty)), owner=owner).is_valid())
        bulk = BulkAddForm(version=self.version, kind='labor', company=self.company)
        self.assertIn(self.staffed, bulk.fields['items'].queryset)
        self.assertNotIn(self.archived, bulk.fields['items'].queryset)
        period = AddPeriodResourceForm(kind='labor', version=self.version, month=fixtures.JAN, company=self.company)
        self.assertIn(self.staffed, period.fields['brigade'].queryset)
        self.assertNotIn(self.empty, period.fields['brigade'].queryset)

    def test_company_scope_merged_entries_and_employee_status_changes(self):
        foreign = Company.objects.create(name='Other planning employees')
        self.employee(self.empty, company=foreign)
        self.assertNotIn(self.empty, planning_brigades(self.company))
        merged = Brigade.objects.create(company=self.company, name='Объединённая должность')
        self.employee(merged)
        BrigadeMerge.objects.create(company=self.company, source=merged, target=self.staffed, created_by=self.planner, audit={'test': True})
        self.assertNotIn(merged, planning_brigades(self.company))
        Employee.objects.filter(company=self.company, position=position_for(self.staffed)).update(is_active=False)
        self.assertNotIn(self.staffed, planning_brigades(self.company))
        self.assertIn(position_for(self.staffed), available_positions(self.company), 'Catalogue crew editing remains available')
        person = self.employee(self.archived)
        self.assertIn(position_for(self.archived), planning_positions(self.company))
        person.is_active = False; person.save(update_fields=['is_active'])
        self.assertNotIn(position_for(self.archived), planning_positions(self.company))

    def test_saved_assignments_without_employees_remain_editable_but_cannot_be_added_again(self):
        position = position_for(self.archived)
        for owner in [self.version, self.schedule]:
            row = WorkResourceAssignment(position=position, count=1, shifts_per_day=1, hours_per_shift=8,
                start_date=fixtures.JAN, end_date=date(2026, 1, 2), include_crew=False)
            saved = work_resources.save(self.manager, owner, row)
            form = InlineAssignmentForm(self.data(position), owner=owner, instance=saved)
            self.assertTrue(form.is_valid(), form.errors)
            self.assertFalse(InlineAssignmentForm(self.data(position), owner=owner).is_valid())
            saved.refresh_from_db(); self.assertEqual(saved.count, 1)
            self.assertEqual(owner.resource_assignments.filter(position=position).count(), 1)

    def test_saved_monthly_rows_keep_only_their_own_exception_in_formset_choices(self):
        rows = [ResourceMonthAllocation.objects.create(company=self.company, version=self.version,
            month=fixtures.JAN, kind='labor', brigade=brigade, count=3) for brigade in [self.archived, self.empty]]
        forms = ResourceAllocationSet(queryset=self.version.resource_allocations.filter(pk__in=[r.pk for r in rows]).order_by('pk'),
            form_kwargs={'version': self.version, 'month': fixtures.JAN, 'company': self.company})
        for form, row, other in zip(forms.forms, rows, reversed(rows)):
            choices = {value.value for value, label in form.fields['brigade'].choices if value}
            self.assertIn(row.brigade_id, choices)
            self.assertIn(self.staffed.pk, choices)
            self.assertNotIn(other.brigade_id, choices)

    def test_links_crew_picker_filters_new_positions_and_retains_captured_recipe(self):
        machine = fixtures.WorkResourceTests.assignment(self, self.version)
        Employee.objects.filter(company=self.company, position=self.position).update(is_active=False)
        data = editor_data(self.version, machine, self.equipment)
        ids = {row['id'] for row in data['positions']}
        self.assertIn(position_for(self.staffed).pk, ids)
        self.assertNotIn(position_for(self.empty).pk, ids)
        self.assertNotIn(position_for(self.archived).pk, ids)
        self.assertIn(self.position.pk, ids, 'Existing crew must not disappear when staffing changes')
        self.client.force_login(self.manager)
        response = self.client.post(reverse('planning:work_resources_crew_gpr', args=[self.version.pk]), {
            'assignment_id': machine.pk, 'revision': data['revision'], 'catalogue_revision': data['catalogue_revision'],
            'crew': [{'position_id': position_for(self.empty).pk, 'count': 1, 'is_replacement': False}]}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        machine.refresh_from_db(); self.assertEqual(len(machine.crew_snapshot), 2)

    def test_actual_add_requests_reject_unstaffed_positions_without_creating_rows(self):
        self.client.force_login(self.manager)
        for owner, route in [(self.version, 'planning:preparation_add_assignment'), (self.schedule, 'planning:work_resources_save_smg')]:
            url = reverse(route, args=[owner.pk])
            response = self.client.post(url, self.data(position_for(self.empty)))
            self.assertEqual(response.status_code, 400)
            self.assertFalse(owner.resource_assignments.exists())
            response = self.client.post(url, self.data(position_for(self.staffed)))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(owner.resource_assignments.count(), 1)


import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('STAFFED_POSITIONS_BROWSER_TESTS') == '1', 'Optional staffed-position Chromium scenario')
class StaffedPositionsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True
    prepare_owner = fixtures.WorkResourceTests.prepare_owner

    def test_new_choices_filtered_and_archived_assignment_can_be_edited_in_both_plans(self):
        fixtures.WorkResourceTests.setUpTestData.__func__(type(self))
        fixtures.WorkResourceTests.setUp(self)
        saved_position = position_for(Brigade.objects.create(company=self.company, name='Ранее запланированный инженер'))
        empty_position = position_for(Brigade.objects.create(company=self.company, name='Незанятая должность'))
        schedule = create(self.planner, self.obj, fixtures.JAN)
        self.prepare_owner(schedule)
        owners = [self.version, schedule]
        assignments = []
        for owner in owners:
            assignments.append(work_resources.save(self.manager, owner, WorkResourceAssignment(
                position=saved_position, start_date=fixtures.JAN, end_date=date(2026, 1, 2), count=1,
                shifts_per_day=1, hours_per_shift=8, include_crew=False)))
        self.client.force_login(self.manager)
        config = {'base': self.live_server_url, 'cookie': self.client.cookies['sessionid'].value,
            'chromium': shutil.which('chromium'), 'staffed': self.position.pk, 'staffed_name': self.position.name,
            'saved': saved_position.pk, 'saved_name': saved_position.name, 'empty': empty_position.pk,
            'plans': [{'url': reverse(route, args=[owner.pk]), 'assignment': row.pk} for route, owner, row in zip(
                ['planning:workspace_edit', 'planning:work_resources_graph_smg'], owners, assignments)]}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/staffed_positions.js'
        result = subprocess.run(['node', str(script)], cwd=script.parents[2], capture_output=True, text=True,
            env=dict(os.environ, STAFFED_POSITIONS_CONFIG=json.dumps(config)), timeout=100)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        for row in assignments:
            row.refresh_from_db(); self.assertEqual(row.count, 3)
        print(result.stdout.strip())
