import json
from datetime import date
from decimal import Decimal
from django.test import TestCase
from . import test_workspace as fixtures
JAN = fixtures.JAN
from .workspace_forms import WorkAllocationForm, WorkAllocationSet
from .workspace_services import WorkspaceService, build_workspace_snapshot
from .models import WorkMonthAllocation


class ConstituentEditTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        workspace = WorkspaceService.create(self.planner, self.obj, 'Items', JAN, date(2026, 1, 31))
        self.version = workspace.baseline_version
        self.row = WorkMonthAllocation.objects.create(company=self.company, version=self.version,
            month=JAN, work=self.composite, quantity=10)

    def form(self, items=None, **extra):
        data = {'work': self.composite.pk, 'month': str(JAN), 'quantity': '10', 'load_profile': ''}
        if items is not None:
            data['edited_item_quantities'] = json.dumps(items)
        data.update(extra)
        return WorkAllocationForm(data, instance=self.row, user=self.planner, version=self.version, month=JAN)

    def test_independent_volumes_saved_and_used_in_daily_plan(self):
        values = {str(self.a.pk): '12', str(self.b.pk): '27'}
        form = self.form(values)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.has_changed())
        row = form.save()
        row.refresh_from_db()
        self.assertEqual(row.item_quantities, values)
        self.assertEqual(row.quantity, Decimal('6'))
        snapshot = build_workspace_snapshot(self.version)
        spec = next(work for work in snapshot['works'] if work['id'] == self.composite.pk)
        totals = {item.pk: sum(Decimal(str(plan['quantity'])) for plan in spec['plans'] if plan['item_id'] == item.pk) for item in [self.a, self.b]}
        self.assertEqual(totals, {self.a.pk: Decimal('12'), self.b.pk: Decimal('27')})
        # Changing the main quantity deliberately restores normative allocation.
        form = self.form(quantity='5', reset_item_quantities='on')
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.item_quantities, {})
        self.assertEqual(row.quantity, Decimal('5'))

    def test_zero_and_invalid_constituent_values(self):
        zero = self.form({str(self.a.pk): '0', str(self.b.pk): '27'})
        self.assertTrue(zero.is_valid(), zero.errors)
        self.assertEqual(zero.save().quantity, 0)
        for values in [{str(self.a.pk): '2'},
                       {str(self.a.pk): '-1', str(self.b.pk): '3'},
                       {str(self.a.pk): '', str(self.b.pk): '3'},
                       {str(self.a.pk): 'NaN', str(self.b.pk): '3'},
                       {'999999999': '1', str(self.b.pk): '3'}, ['1'], {}]:
            with self.subTest(values=values):
                form = self.form(values)
                self.assertFalse(form.is_valid())
                self.assertTrue(form.non_field_errors())

    def test_unchanged_submission_preserves_imported_volumes(self):
        self.row.item_quantities = {str(self.a.pk): '20', str(self.b.pk): '36'}
        self.row.save()
        form = self.form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().item_quantities, self.row.item_quantities)

    def test_formset_saves_when_only_constituent_quantities_change(self):
        values = {str(self.a.pk): '20', str(self.b.pk): '45'}
        formset = WorkAllocationSet({
            'works-TOTAL_FORMS': '1', 'works-INITIAL_FORMS': '1',
            'works-0-id': str(self.row.pk), 'works-0-work': str(self.composite.pk),
            'works-0-month': str(JAN), 'works-0-quantity': '10',
            'works-0-load_profile': '', 'works-0-edited_item_quantities': json.dumps(values),
        }, prefix='works', queryset=WorkMonthAllocation.objects.filter(pk=self.row.pk),
            form_kwargs={'user': self.planner, 'version': self.version, 'month': JAN})
        self.assertTrue(formset.is_valid(), formset.errors)
        self.assertEqual(len(formset.save()), 1)
        self.row.refresh_from_db()
        self.assertEqual(self.row.item_quantities, values)
        self.assertEqual(self.row.quantity, 10)
