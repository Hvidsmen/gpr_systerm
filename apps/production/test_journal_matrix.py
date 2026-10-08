from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role
from . import test_day_workspace as fixtures
from .views.resources import CONFIG
from .models import DailyFact


class JournalMatrixTests(TestCase):
    setUpTestData = classmethod(fixtures.FactDayWorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.admin = User.objects.create_user(username='journal-admin', company=self.company,
                                              role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)
        self.params = {'start': self.day.isoformat(), 'end': (self.day + timedelta(days=2)).isoformat()}

    def record(self, model, kind, obj=None, day=None, quantity=2, **extra):
        identity = {'labor': {'brigade': self.brigade}, 'equipment': {'equipment_type': self.equipment},
                    'fuel': {'fuel_type': 'DIESEL'}}[kind]
        quantity_field = CONFIG[kind][3 if model == CONFIG[kind][0] else 4]
        return model.objects.create(company=self.company, construction_object=obj or self.obj,
                                   date=day or self.day, **identity, **{quantity_field: quantity}, **extra)

    def test_single_delete_get_then_empty_post_for_all_six_journals(self):
        for kind, (plan, fact, *_) in CONFIG.items():
            for suffix, model in [('plan', plan), ('fact', fact)]:
                with self.subTest(kind=kind, suffix=suffix):
                    row = self.record(model, kind)
                    url = reverse('production:' + kind + '_' + suffix + '_delete', args=[row.pk])
                    response = self.client.get(url, {'return_query': 'start=2026-10-01&end=2026-10-31'})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(model.objects.filter(pk=row.pk).exists())
                    response = self.client.post(url, {'return_query': 'start=2026-10-01&end=2026-10-31'})
                    self.assertEqual(response.status_code, 302)
                    self.assertIn('start=2026-10-01', response.url)
                    self.assertFalse(model.objects.filter(pk=row.pk).exists())

    def test_matrices_dates_zero_missing_and_resource_identities(self):
        for kind, (plan, fact, *_) in CONFIG.items():
            for suffix, model in [('plan', plan), ('fact', fact)]:
                with self.subTest(kind=kind, suffix=suffix):
                    first = self.record(model, kind, quantity=0)
                    second = self.record(model, kind, day=self.day + timedelta(days=1), quantity=3)
                    self.record(model, kind, obj=self.other_obj, quantity=99)
                    if kind == 'equipment':
                        self.record(model, kind, equipment_number='OTHER')
                    elif kind == 'fuel':
                        self.record(model, kind, equipment_ref='OTHER')
                    response = self.client.get(reverse('production:' + kind + '_' + suffix + '_list'), self.params)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(len(response.context['matrix_days']), 3)
                    row = next(r for r in response.context['matrix_rows'] if r['cells'][0] and r['cells'][0]['record'].pk == first.pk)
                    self.assertEqual(row['total'], 3)
                    self.assertEqual(row['cells'][1]['record'].pk, second.pk)
                    self.assertIsNone(row['cells'][2])
                    self.assertContains(response, 'Удалить выбранные')
                    self.assertContains(response, 'Изменить')

    def test_bulk_preview_then_confirm_deletes_only_selected_for_all_six(self):
        for kind, (plan, fact, *_) in CONFIG.items():
            for suffix, model in [('plan', plan), ('fact', fact)]:
                with self.subTest(kind=kind, suffix=suffix):
                    first = self.record(model, kind)
                    second = self.record(model, kind, day=self.day + timedelta(days=1))
                    keep = self.record(model, kind, obj=self.other_obj)
                    url = reverse('production:journal_' + suffix + '_bulk_delete', kwargs={'kind': kind + '_' + suffix})
                    response = self.client.post(url, {'selected': [first.pk, second.pk], 'return_query': 'start=2026-10-01'})
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(model.objects.filter(pk=first.pk).exists())
                    response = self.client.post(url, {'confirm': '1', 'selection': response.context['selection'], 'return_query': 'start=2026-10-01'})
                    self.assertEqual(response.status_code, 302)
                    self.assertFalse(model.objects.filter(pk__in=[first.pk, second.pk]).exists())
                    self.assertTrue(model.objects.filter(pk=keep.pk).exists())

    def test_foreman_bulk_scope_and_readonly_controls(self):
        own = self.record(CONFIG['labor'][1], 'labor')
        other = self.record(CONFIG['labor'][1], 'labor', obj=self.other_obj)
        self.client.force_login(self.user)
        url = reverse('production:journal_fact_bulk_delete', kwargs={'kind': 'labor_fact'})
        self.assertEqual(self.client.post(url, {'selected': [own.pk, other.pk]}).status_code, 403)
        self.assertTrue(CONFIG['labor'][1].objects.filter(pk=own.pk).exists())
        response = self.client.get(reverse('production:labor_fact_list'), self.params)
        self.assertEqual(len(response.context['matrix_rows']), 1)
        self.client.force_login(User.objects.create_user(username='journal-reader', company=self.company,
                                                          role=Role.objects.get(code='MANAGER')))
        response = self.client.get(reverse('production:labor_fact_list'), self.params)
        self.assertNotContains(response, 'bulk-delete-form')
        self.assertEqual(self.client.post(url, {'selected': [own.pk]}).status_code, 403)

    def test_work_facts_bulk_and_tampered_confirmation(self):
        row = DailyFact.objects.create(company=self.company, project_work=self.work, date=self.day, actual_quantity=2)
        url = reverse('production:journal_fact_bulk_delete', kwargs={'kind': 'work_facts'})
        response = self.client.post(url, {'selected': [row.pk]})
        token = response.context['selection']
        self.client.post(url, {'confirm': '1', 'selection': token + 'bad'})
        self.assertTrue(DailyFact.objects.filter(pk=row.pk).exists())
        self.client.post(url, {'confirm': '1', 'selection': token})
        self.assertFalse(DailyFact.objects.filter(pk=row.pk).exists())

    def test_invalid_period_and_unavailable_object_show_errors(self):
        url = reverse('production:fuel_fact_list')
        self.assertTrue(self.client.get(url, {**self.params, 'end': 'bad'}).context['filter_form'].errors)
        self.client.force_login(self.user)
        self.assertTrue(self.client.get(url, {**self.params, 'construction_object': self.other_obj.pk}).context['filter_form'].errors)

    def test_monthly_matrix_and_bulk_deletion_preserve_work_facts(self):
        from apps.planning.models import MonthlyPlan
        from datetime import date
        plans = [MonthlyPlan.objects.create(company=self.company, project_work=self.work,
            year=2026, month=month, start_date=date(2026, month, 1), end_date=date(2026, month, 28),
            planned_quantity=month) for month in [1, 2]]
        fact = DailyFact.objects.create(company=self.company, project_work=self.work, date=self.day, actual_quantity=3)
        response = self.client.get(reverse('planning:plan_list'))
        self.assertEqual(len(response.context['matrix_rows']), 1)
        self.assertEqual(len(response.context['matrix_months']), 2)
        self.assertContains(response, 'Удалить выбранные')
        self.assertContains(response, '01.2026')
        url = reverse('planning:journal_bulk_delete', kwargs={'kind': 'monthly_plans'})
        response = self.client.post(url, {'selected': [p.pk for p in plans]})
        self.assertEqual(response.status_code, 200)
        self.client.post(url, {'confirm': '1', 'selection': response.context['selection']})
        self.assertFalse(MonthlyPlan.objects.filter(pk__in=[p.pk for p in plans]).exists())
        self.assertTrue(DailyFact.objects.filter(pk=fact.pk).exists())

    def test_monthly_sources_block_single_and_bulk_deletion(self):
        from apps.planning.models import MonthlyPlan, PlanVersion, GlobalPlanVersion
        plan = MonthlyPlan.objects.create(company=self.company, project_work=self.work,
            year=2026, month=10, start_date=self.day, end_date=self.day, planned_quantity=2)
        version = PlanVersion.objects.create(company=self.company, monthly_plan=plan,
            version_number=1, created_by=self.admin)
        global_version = GlobalPlanVersion.objects.create(company=self.company, construction_object=self.obj,
            version_number=1, start_date=self.day, end_date=self.day)
        global_version.source_versions.add(version)
        url = reverse('planning:plan_delete', args=[plan.pk])
        self.assertContains(self.client.get(url), 'источник необходимо сохранить')
        response = self.client.post(url)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(MonthlyPlan.objects.filter(pk=plan.pk).exists())
        url = reverse('planning:journal_bulk_delete', kwargs={'kind': 'monthly_plans'})
        response = self.client.post(url, {'selected': [plan.pk]})
        self.assertTrue(response.context['blocked'])
        response = self.client.post(url, {'confirm': '1', 'selection': response.context['selection']})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(MonthlyPlan.objects.filter(pk=plan.pk).exists())

    def test_bulk_failure_rolls_back_entire_selection(self):
        from unittest.mock import patch
        from django.db.models.query import QuerySet
        from django.core.exceptions import ValidationError
        model = CONFIG['fuel'][1]
        first = self.record(model, 'fuel')
        second = self.record(model, 'fuel', day=self.day + timedelta(days=1))
        url = reverse('production:journal_fact_bulk_delete', kwargs={'kind': 'fuel_fact'})
        preview = self.client.post(url, {'selected': [first.pk, second.pk]})
        original = QuerySet.delete
        def fail_after_delete(query):
            original(query)
            raise ValidationError('Delete failed')
        with patch.object(QuerySet, 'delete', fail_after_delete):
            response = self.client.post(url, {'confirm': '1', 'selection': preview.context['selection']})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(model.objects.filter(pk__in=[first.pk, second.pk]).count(), 2)

    def test_confirmation_cannot_be_used_by_another_user_or_kind(self):
        row = self.record(CONFIG['fuel'][1], 'fuel')
        url = reverse('production:journal_fact_bulk_delete', kwargs={'kind': 'fuel_fact'})
        preview = self.client.post(url, {'selected': [row.pk]})
        token = preview.context['selection']
        other_url = reverse('production:journal_fact_bulk_delete', kwargs={'kind': 'labor_fact'})
        self.assertEqual(self.client.post(other_url, {'confirm': '1', 'selection': token}).status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {'confirm': '1', 'selection': token}).status_code, 302)
        self.assertTrue(CONFIG['fuel'][1].objects.filter(pk=row.pk).exists())

    def test_single_monthly_delete_preserves_facts_and_returns_filters(self):
        from apps.planning.models import MonthlyPlan
        plan = MonthlyPlan.objects.create(company=self.company, project_work=self.work,
            year=2026, month=10, start_date=self.day, end_date=self.day, planned_quantity=2)
        fact = DailyFact.objects.create(company=self.company, project_work=self.work,
                                       date=self.day, actual_quantity=3)
        url = reverse('planning:plan_delete', args=[plan.pk])
        response = self.client.post(url, {'return_query': 'work=' + str(self.work.pk)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('planning:plan_list') + '?work=' + str(self.work.pk))
        self.assertFalse(MonthlyPlan.objects.filter(pk=plan.pk).exists())
        self.assertTrue(DailyFact.objects.filter(pk=fact.pk).exists())
