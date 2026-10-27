from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from apps.audit.models import AuditLog
from apps.production.models import DailyFact
from . import test_daily_schedule as fixtures, daily_schedule
from .daily_schedule_approval import transition
from .models import DailySchedule, WorkTask
from .smg_rework import return_applied


class SMGReworkTests(TestCase):
    setUpTestData = classmethod(fixtures.DailyScheduleTests.setUpTestData.__func__)
    prepare = fixtures.DailyScheduleTests.prepare
    values = fixtures.DailyScheduleTests.values

    def setUp(self):
        self.admin = User.objects.create_user(username='smg-rework-admin', company=self.company, role=Role.objects.get(code='ADMIN'))
        self.prepare()
        daily_schedule.save_entry(self.planner, self.entry, self.values(self.entry, 120), 0)
        fixtures.activate(self.planner, self.schedule)
        self.schedule.refresh_from_db(); self.entry.refresh_from_db()

    def reopen(self, comment='Уточнить дневные объёмы', version=None):
        return return_applied(self.admin, self.schedule, comment, version or self.schedule.updated_at.isoformat())

    def test_return_preserves_plan_round_facts_locks_and_removes_operative_overlay(self):
        self.schedule.locked_before = date(2026, 2, 2)
        self.schedule.save(update_fields=['locked_before', 'updated_at'])
        before = list(self.schedule.entries.order_by('pk').values())
        review = self.schedule.approval_reviews.get()
        frozen = deepcopy(review.snapshot)
        decisions = set(review.decisions.values_list('pk', flat=True))
        fact = DailyFact.objects.create(company=self.company, project_work=self.simple, date=date(2026, 2, 1), actual_quantity=7)
        reopened = self.reopen()
        self.assertEqual((reopened.pk, reopened.revision, reopened.status), (self.schedule.pk, 1, 'DRAFT'))
        self.assertEqual(reopened.locked_before, date(2026, 2, 2))
        self.assertEqual(list(reopened.entries.order_by('pk').values()), before)
        self.entry.refresh_from_db()
        with self.assertRaises(ValidationError):
            daily_schedule.save_entry(self.admin, self.entry, self.values(self.entry, 70, day=1), self.entry.revision)
        fact.refresh_from_db(); self.assertEqual(fact.actual_quantity, 7)
        review.refresh_from_db(); self.assertEqual(review.snapshot, frozen)
        self.assertTrue(decisions <= set(review.decisions.values_list('pk', flat=True)))
        self.assertEqual(review.decisions.get(action='REJECT').actor_id, self.admin.pk)
        self.source.refresh_from_db(); self.assertEqual(self.source.snapshot, self.original)
        self.assertFalse(DailySchedule.objects.filter(construction_object=self.obj, month=self.schedule.month, status='APPLIED').exists())
        self.assertEqual(daily_schedule.overlay(self.source, self.original, self.schedule.month, date(2026, 2, 28)), self.original)
        audit = AuditLog.objects.get(entity_type='planning.dailyschedule', entity_id=str(reopened.pk), action='REJECT')
        self.assertEqual(audit.old_value['entries'][0]['rows'], before[0]['rows'])
        self.assertEqual(audit.new_value['status'], 'DRAFT')
        self.assertEqual(audit.user_id, self.admin.pk)
        self.assertTrue(WorkTask.objects.filter(schedule=reopened, key__startswith='smg-repair:', status='OPEN').exists())

    def test_old_checks_cannot_reapply_and_repeat_cycle_gets_new_approval_round(self):
        first_version = self.schedule.updated_at.isoformat()
        self.schedule = self.reopen()
        self.assertFalse(self.schedule.preparation.resources_ready_at)
        self.assertEqual(self.schedule.estimate_reviews.latest('pk').status, 'REJECTED')
        with self.assertRaises(ValidationError): daily_schedule.activate(self.admin, self.schedule)
        with self.assertRaises(ValidationError): transition(self.admin, self.schedule, 'submit')
        fixtures.activate(self.planner, self.schedule)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, 'APPLIED')
        self.assertEqual(self.schedule.approval_round, 2)
        with self.assertRaisesMessage(ValidationError, 'уже изменён'):
            return_applied(self.admin, self.schedule, 'Устаревшая форма', first_version)
        self.reopen('Повторное уточнение')
        self.assertEqual(self.schedule.approval_reviews.filter(decisions__action='REJECT').distinct().count(), 2)

    def test_only_admin_and_same_company_can_return_applied_schedule(self):
        for user in [self.planner, self.manager, self.approvers['CEO']]:
            with self.assertRaises(PermissionDenied): return_applied(user, self.schedule, 'Причина', self.schedule.updated_at.isoformat())
        foreign = User.objects.create_user(username='foreign-smg-admin', company=Company.objects.create(name='Other'), role=Role.objects.get(code='ADMIN'))
        with self.assertRaises(PermissionDenied): return_applied(foreign, self.schedule, 'Причина', self.schedule.updated_at.isoformat())
        self.schedule.refresh_from_db(); self.assertEqual(self.schedule.status, 'APPLIED')

    def test_blank_reason_stale_form_and_competing_revision_leave_plan_applied(self):
        for reason,version in [('', self.schedule.updated_at.isoformat()), ('   ', self.schedule.updated_at.isoformat()), ('Причина','invalid'), ('Причина', '2020-01-01T00:00:00+00:00')]:
            with self.assertRaises(ValidationError): return_applied(self.admin, self.schedule, reason, version)
        other = DailySchedule.objects.create(company=self.company, construction_object=self.obj, month=self.schedule.month, revision=2, created_by=self.planner)
        with self.assertRaisesMessage(ValidationError, 'незавершённая редакция'): self.reopen()
        self.schedule.refresh_from_db(); self.assertEqual(self.schedule.status, 'APPLIED')
        self.assertEqual(other.status, 'DRAFT')
        self.assertFalse(self.schedule.approval_reviews.get().decisions.filter(action='REJECT').exists())

    def test_audit_failure_rolls_back_status_checks_and_decision(self):
        old = self.schedule.preparation.resources_ready_at
        with patch('apps.planning.smg_rework.AuditLog.objects.create', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError): self.reopen()
        self.schedule.refresh_from_db(); self.assertEqual(self.schedule.status, 'APPLIED')
        self.assertEqual(self.schedule.preparation.resources_ready_at, old)
        self.assertEqual(self.schedule.estimate_reviews.latest('pk').status, 'APPROVED')
        self.assertFalse(self.schedule.approval_reviews.get().decisions.filter(action='REJECT').exists())

    def test_legacy_applied_schedule_without_round_has_a_preserved_snapshot(self):
        from .independent_smg import create
        legacy = create(self.admin, self.obj, date(2026, 3, 1))
        legacy.status = 'APPLIED'; legacy.save(update_fields=['status', 'updated_at'])
        result = return_applied(self.admin, legacy, 'Проверить старый СМГ', legacy.updated_at.isoformat())
        self.assertEqual(result.status, 'DRAFT')
        review = result.approval_reviews.get()
        self.assertEqual(review.number, 1)
        self.assertEqual(review.decisions.get().comment, 'Проверить старый СМГ')

    def test_http_action_is_admin_only_and_header_opens_same_revision(self):
        detail = reverse('planning:daily_schedule_detail', args=[self.schedule.pk])
        action = reverse('planning:daily_schedule_apply', args=[self.schedule.pk])
        self.client.force_login(self.planner)
        self.assertNotContains(self.client.get(detail), 'data-smg-open-rework')
        self.assertEqual(self.client.post(action, {'action':'return_applied', 'comment':'Причина', 'version':self.schedule.updated_at.isoformat()}).status_code, 403)
        self.client.force_login(self.admin)
        response = self.client.get(detail)
        self.assertContains(response, 'data-smg-open-rework')
        self.assertContains(response, 'Внесённый факт, закрытые дни и история согласования сохранятся.')
        result = self.client.post(action, {'action':'return_applied', 'comment':'Уточнить ресурсы', 'version':self.schedule.updated_at.isoformat()}, follow=True)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.context['table_editable'])
        self.assertContains(result, 'На доработке')
        self.assertContains(result, 'Уточнить ресурсы')
        self.assertContains(result, 'Передать повторно руководителю и сметчику')
        self.assertNotContains(result, 'data-smg-open-rework')
        # A double click or stale form cannot append a duplicate return decision.
        self.client.post(action, {'action':'return_applied', 'comment':'Ещё раз', 'version':self.schedule.updated_at.isoformat()})
        self.assertEqual(self.schedule.approval_reviews.get().decisions.filter(action='REJECT').count(), 1)


@skipUnless(os.environ.get('SMG_REWORK_BROWSER_TESTS') == '1', 'Optional SMG return Chromium scenario')
class SMGReworkBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True
    prepare = fixtures.DailyScheduleTests.prepare
    values = fixtures.DailyScheduleTests.values

    def test_return_dialog_reason_edit_same_revision_and_mobile(self):
        fixtures.DailyScheduleTests.setUpTestData.__func__(type(self))
        SMGReworkTests.setUp(self)
        self.schedule.locked_before = date(2026, 2, 2)
        self.schedule.save(update_fields=['locked_before', 'updated_at'])
        fact = DailyFact.objects.create(company=self.company, project_work=self.simple, date=date(2026, 2, 1), actual_quantity=7)
        frozen = deepcopy(self.schedule.approval_reviews.get().snapshot)
        def cookie(user):
            client = Client(); client.force_login(user)
            return client.cookies['sessionid'].value
        config = {'base': self.live_server_url, 'path': reverse('planning:daily_schedule_detail', args=[self.schedule.pk]),
            'cookie': cookie(self.admin), 'planner_cookie': cookie(self.planner), 'entry': self.entry.pk, 'chromium': shutil.which('chromium')}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/smg_rework.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, timeout=120,
            env=dict(os.environ, SMG_REWORK_CONFIG=json.dumps(config)))
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        self.schedule.refresh_from_db(); self.entry.refresh_from_db(); fact.refresh_from_db()
        self.assertEqual(self.schedule.status, 'DRAFT')
        self.assertEqual(self.schedule.revision, 1)
        self.assertEqual(sum(Decimal(row['quantity']) for row in self.entry.rows), 130)
        self.assertEqual(fact.actual_quantity, 7)
        self.assertEqual(self.schedule.approval_reviews.get().snapshot, frozen)
        print(result.stdout.strip())
