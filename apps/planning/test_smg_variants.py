from copy import deepcopy
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role, Company
from . import independent_smg as service, smg_comparison
from .models import DailySchedule, DailyScheduleEntry
from .test_independent_smg import IndependentSMGTests as Fixture
from .test_daily_schedule import activate as approve_and_activate
from .daily_schedule_approval import transition
from .daily_schedule import activate, identities, save_entry


class SMGVariantTests(TestCase):
    setUpTestData = classmethod(Fixture.setUpTestData.__func__)
    def add(self,schedule,quantity=7):
        existing=schedule.entries.filter(kind='work',metadata__id=self.simple.pk).first()
        if existing is None:return Fixture.add(self,schedule,quantity)
        return save_entry(self.planner,existing,[{'quantity':quantity if day.day==5 else 0} for day,_ in identities(existing)],existing.revision)
    source = Fixture.source

    def new(self, name='Вариант', source=None):
        return service.create(self.planner, self.obj, self.month, source, name=name)

    def setUp(self):
        from .test_workspace import JAN
        self.month = JAN
        self.client.force_login(self.planner)

    def test_independent_drafts_same_object_month_have_names_and_do_not_reuse_rows(self):
        first, second = self.new('Базовый'), self.new('Ускоренный')
        entry = self.add(first, 7)
        self.assertEqual((first.revision, second.revision), (1, 2))
        self.assertNotEqual(first.pk, second.pk)
        self.assertFalse(second.entries.exists())
        self.add(second, 12)
        entry.refresh_from_db()
        self.assertEqual(sum(D(r['quantity']) for r in entry.rows), 7)
        self.assertEqual(DailySchedule.objects.filter(status='DRAFT').count(), 2)
        self.assertEqual(first.name, 'Базовый')

    def test_create_form_allows_another_variant_and_list_links_comparison(self):
        endpoint = reverse('planning:smg_create')
        for name in ['Основной', 'Альтернатива']:
            response = self.client.post(endpoint, {'construction_object': self.obj.pk, 'month': '2026-01', 'name': name})
            self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(DailySchedule.objects.count(), 2)
        page = self.client.get(reverse('planning:daily_schedule_list'))
        self.assertContains(page, 'Альтернатива')
        self.assertContains(page, 'Сравнить')
        self.assertContains(page, 'Копировать вариант')

    def test_copy_is_an_unreviewed_independent_draft(self):
        first = self.new('Проработка'); original = self.add(first, 7)
        first.locked_before = self.month + timedelta(days=2); first.save()
        second = service.revise(self.planner, first, independent=True)
        self.assertEqual(second.status, 'DRAFT')
        self.assertEqual(second.replaces_id, first.pk)
        self.assertEqual(second.name, 'Проработка — копия')
        self.assertEqual(second.locked_before, first.locked_before)
        self.assertFalse(second.approval_reviews.exists())
        self.assertFalse(second.estimate_reviews.exists())
        copied = second.entries.get()
        self.assertEqual(copied.rows, original.rows)
        self.assertNotEqual(copied.pk, original.pk)
        values = [{'quantity': 11 if day.day == 5 else 0} for day, _ in identities(copied)]
        save_entry(self.planner, copied, values, copied.revision)
        original.refresh_from_db()
        self.assertEqual(sum(D(r['quantity']) for r in original.rows), 7)

    def test_comparison_detects_shifted_days_even_when_month_totals_match(self):
        first, second = self.new('Один'), self.new('Два')
        self.add(first, 7); entry = self.add(second, 7)
        for row in entry.rows:
            row['quantity'] = '7' if row['date'] == '2026-01-06' else '0'
        entry.save(update_fields=['rows'])
        sections = smg_comparison.compare(first, second)
        row = sections[0]['rows'][0]
        self.assertEqual((row['left'], row['right'], row['delta']), (7, 7, 0))
        self.assertTrue(row['different'])
        self.assertEqual({r['date'].day: r['delta'] for r in row['days'] if r['delta']}, {5: -7, 6: 7})
        page = self.client.get(reverse('planning:smg_compare', args=[first.pk]), {'other': second.pk, 'differences': 'on'})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Сравнение СМГ')
        self.assertContains(page, 'Исходный')

    def test_comparison_averages_counts_sums_hours_and_includes_missing_positions(self):
        first, second = self.new(), self.new()
        rows = [{'date': f'2026-01-{day:02}', 'brigade_id': self.brigade.pk, 'planned_count': '2', 'planned_hours': '16'} for day in range(1, 32)]
        DailyScheduleEntry.objects.create(schedule=second, key='labor:variant', kind='labor', label='Специалист', rows=rows)
        entries = smg_comparison.compare(first, second)[0]['rows']
        self.assertEqual({r['metric']: r['right'] for r in entries}, {'Средняя численность': 2, 'Часы': 496})
        self.assertTrue(all(r['missing_left'] for r in entries))

    def test_only_one_approved_or_applied_variant_is_allowed_by_database(self):
        first, second = self.new(), self.new()
        DailySchedule.objects.filter(pk=first.pk).update(status='APPLIED')
        with self.assertRaises(IntegrityError), transaction.atomic():
            DailySchedule.objects.filter(pk=second.pk).update(status='APPROVED')
        second.refresh_from_db(); self.assertEqual(second.status, 'DRAFT')

    def test_new_approval_replaces_operative_variant_atomically_and_keeps_history(self):
        source = self.source()
        first = self.new('Ранний', source); self.add(first, 7)
        first = approve_and_activate(self.planner, first)
        frozen = deepcopy(list(first.entries.values('key', 'rows')))
        review = first.approval_reviews.get(); decisions = review.decisions.count()
        second = self.new('Выбранный', source); self.add(second, 12)
        second = approve_and_activate(self.planner, second)
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('CANCELLED', 'APPLIED'))
        self.assertEqual(DailySchedule.objects.filter(status__in=['APPROVED', 'APPLIED']).count(), 1)
        self.assertEqual(list(first.entries.values('key', 'rows')), frozen)
        self.assertEqual(review.decisions.count(), decisions+1)
        self.assertEqual(review.decisions.filter(action='CANCEL').count(),1)
        source.refresh_from_db(); self.assertEqual(source.snapshot['works'][0]['name'], self.simple.name)
        # Retrying the application button does not replace or erase anything.
        self.assertEqual(activate(self.manager, second).pk, second.pk)

    def test_unchecked_candidate_cannot_replace_operative_schedule(self):
        first = self.new(); self.add(first, 7); first = approve_and_activate(self.planner, first)
        second = self.new(); self.add(second, 12)
        with self.assertRaises(ValidationError):
            activate(self.manager, second)
        first.refresh_from_db(); self.assertEqual(first.status, 'APPLIED')
        second.refresh_from_db(); self.assertEqual(second.status, 'DRAFT')

    def test_comparison_and_copy_cannot_access_other_company_or_object(self):
        first, second = self.new(), self.new()
        other = DailySchedule.objects.create(company=self.company, construction_object=self.obj, month=self.month.replace(month=2), revision=1, created_by=self.planner)
        page = self.client.get(reverse('planning:smg_compare', args=[first.pk]), {'other': other.pk})
        self.assertIsNone(page.context['other'])
        outsider = User.objects.create_user(username='variant-foreign', company=Company.objects.create(name='Foreign'), role=Role.objects.get(code='ADMIN'))
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse('planning:smg_compare', args=[first.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('planning:smg_copy_variant', args=[first.pk])).status_code, 404)
        self.client.force_login(self.approvers['HR_HEAD'])
        self.assertEqual(self.client.get(reverse('planning:smg_compare', args=[first.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse('planning:smg_copy_variant', args=[first.pk])).status_code, 403)

    def test_failed_replacement_leaves_old_schedule_operative(self):
        first = self.new(); self.add(first, 7); first = approve_and_activate(self.planner, first)
        second = self.new(); self.add(second, 12)
        from .approval_test_helpers import estimator_check
        estimator_check(second, self.planner)
        second = transition(second.preparation.manager, second, 'submit')
        for role, action in [('PRODUCTION_HEAD', 'approve_production'), ('HR_HEAD', 'approve_hr'), ('TECH_HEAD', 'approve_tech'), ('SECURITY_HEAD', 'approve_security'), ('SBO_HEAD', 'approve_sbo')]:
            second = transition(self.approvers[role], second, action)
        with patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                from .smg_variants import cancel_approval
                cancel_approval(self.approvers['CEO'],first,'Причина отмены',first.updated_at.isoformat())
        first.refresh_from_db(); second.refresh_from_db()
        self.assertEqual((first.status, second.status), ('APPLIED', 'SUBMITTED'))
        self.assertFalse(second.approval_reviews.get().decisions.filter(section='CEO').exists())

    def test_closed_days_cannot_be_replaced_with_different_variant_quantities(self):
        from .smg_variants import require_available, cancel_approval
        first = self.new(); self.add(first,7); first=approve_and_activate(self.planner,first)
        first.locked_before=self.month+timedelta(days=10);first.save(update_fields=['locked_before'])
        second=self.new();self.add(second,12)
        cancel_approval(self.approvers['CEO'],first,'Смена варианта',first.updated_at.isoformat())
        with self.assertRaisesMessage(ValidationError,'закрытые дни'), transaction.atomic():
            require_available(second)
        first.refresh_from_db();self.assertEqual(first.status,'CANCELLED')

    def test_final_approval_is_blocked_until_explicit_cancellation(self):
        first=self.new('Действующий');self.add(first,7);first=approve_and_activate(self.planner,first)
        second=self.new('Другой');self.add(second,12)
        from .approval_test_helpers import estimator_check
        estimator_check(second,self.planner)
        second=transition(second.preparation.manager,second,'submit')
        for role,action in [('PRODUCTION_HEAD','approve_production'),('HR_HEAD','approve_hr'),('TECH_HEAD','approve_tech'),('SECURITY_HEAD','approve_security'),('SBO_HEAD','approve_sbo')]:
            second=transition(self.approvers[role],second,action)
        with self.assertRaisesMessage(ValidationError,'Сначала явно отмените'):
            transition(self.approvers['CEO'],second,'approve')
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual((first.status,second.status),('APPLIED','SUBMITTED'))
        from .smg_variants import cancel_approval
        cancel_approval(self.approvers['CEO'],first,'Выбран другой вариант',first.updated_at.isoformat())
        second=transition(self.approvers['CEO'],second,'approve')
        self.assertEqual(second.status,'APPROVED')
        self.assertEqual(activate(second.preparation.manager,second).status,'APPLIED')

    def test_cancellation_roles_reason_version_and_confirmation_page(self):
        first=self.new();self.add(first,7);first=approve_and_activate(self.planner,first)
        from .smg_variants import cancel_approval
        for actor in [self.planner,self.manager,self.approvers['HR_HEAD']]:
            with self.assertRaises(PermissionDenied):cancel_approval(actor,first,'Причина',first.updated_at.isoformat())
        for reason,version in [('',first.updated_at.isoformat()),('Причина','invalid')]:
            with self.assertRaises(ValidationError):cancel_approval(self.approvers['CEO'],first,reason,version)
        self.client.force_login(self.approvers['CEO'])
        endpoint=reverse('planning:smg_cancel_approval',args=[first.pk]);page=self.client.get(endpoint)
        self.assertContains(page,'перестанет использоваться')
        version=page.context['form'].initial['version']
        self.assertEqual(self.client.post(endpoint,{'version':version,'reason':'Изменение сценария','confirm':'on'}).status_code,302)
        first.refresh_from_db();self.assertEqual(first.status,'CANCELLED')
        self.assertTrue(first.approval_reviews.get().decisions.filter(action='CANCEL',comment='Изменение сценария').exists())
