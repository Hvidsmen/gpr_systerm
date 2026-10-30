import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from datetime import timedelta
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from importlib import import_module
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.http import Http404
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company, Role, User
from .models import WorkTask, WorkTaskEvent
from . import task_services as service
from .task_admin import filtered


class TaskQueueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Task queues')
        role = Role.objects.get(code='MANAGER')
        cls.author = User.objects.create_user(username='queue-author', company=cls.company, role=role)
        cls.worker = User.objects.create_user(username='queue-worker', company=cls.company, role=role)
        cls.other = User.objects.create_user(username='queue-other', company=cls.company, role=role)
        cls.admin = User.objects.create_user(username='queue-admin', company=cls.company, role=Role.objects.get(code='ADMIN'))
        cls.observer = User.objects.create_user(username='queue-observer', company=cls.company, role=Role.objects.get(code='OBSERVER'))

    def task(self, key, **kwargs):
        values = dict(kind='MANUAL', title=key, created_by=self.author, assigned_to=self.worker)
        values.update(kwargs)
        return service.ensure(self.company, key, **values)

    def page(self, user=None, **params):
        self.client.force_login(user or self.worker)
        response = self.client.get(reverse('planning:task_list'), params)
        self.assertEqual(response.status_code, 200)
        return response

    def ids(self, response):
        return {task.pk for task in response.context['page']}

    def test_default_incoming_excludes_own_outgoing_and_unrelated(self):
        incoming = self.task('incoming')
        role = self.task('role', assigned_to=None, target_role='MANAGER')
        self.task('outgoing', assigned_to=self.other, created_by=self.worker)
        self.task('unrelated', assigned_to=self.other)
        page = self.page()
        self.assertEqual(self.ids(page), {incoming.pk, role.pk})
        self.assertEqual(page.context['scope'], 'incoming')
        self.assertContains(page, 'Задания мне')
        self.assertContains(page, 'Задания от меня')
        self.assertNotContains(page, '>Все доступные</a>')
        self.assertEqual(self.ids(self.page(scope='all')), {incoming.pk, role.pk})

    def test_both_directions_have_distinct_stages_and_counts(self):
        new = self.task('new')
        started = self.task('started'); service.take(self.worker, started)
        review = self.task('review', status='IN_REVIEW')
        done = self.task('done', status='DONE')
        cancelled = self.task('cancelled', status='SUPERSEDED')
        closed = self.task('closed', status='CLOSED_ADMIN')
        for user, scope in [(self.worker, 'incoming'), (self.author, 'outgoing')]:
            for status, expected in [('not_taken', {new.pk}), ('in_progress', {started.pk, review.pk}), ('history', {done.pk, cancelled.pk, closed.pk})]:
                page = self.page(user, scope=scope, status=status, kind='MANUAL')
                self.assertEqual(self.ids(page), expected)
                self.assertEqual([stage['count'] for stage in page.context['stages']], [1, 2, 3])

    def test_acceptance_is_audited_idempotent_and_role_claim_is_exclusive(self):
        task = self.task('claim', assigned_to=None, target_role='MANAGER')
        service.take(self.worker, task); service.take(self.worker, task)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to, self.worker)
        self.assertIsNotNone(task.started_at)
        self.assertEqual(task.status, 'OPEN')
        self.assertEqual(task.progress_label, 'В работе')
        self.assertEqual(task.events.filter(action='Принято в работу').count(), 1)
        self.assertEqual(task.events.get(action='Принято в работу').actor, self.worker)
        with self.assertRaises(Http404): service.take(self.other, task)
        self.assertNotIn(task.pk, self.ids(self.page(self.other)))

    def test_take_post_does_not_happen_on_view_and_respects_rights(self):
        task = self.task('post')
        url = reverse('planning:task_detail', args=[task.pk])
        self.client.force_login(self.worker)
        self.assertContains(self.client.get(url), 'Взять в работу')
        task.refresh_from_db(); self.assertIsNone(task.started_at)
        self.assertEqual(self.client.post(url, {'action': 'take'}).status_code, 302)
        self.assertContains(self.client.get(url), 'В работе')
        self.client.force_login(self.author)
        self.assertEqual(self.client.post(url, {'action': 'take'}).status_code, 403)
        self.client.force_login(self.observer)
        self.assertEqual(self.client.post(url, {'action': 'take'}).status_code, 403)
        self.assertNotContains(self.client.get(url), 'value="take"')

    def test_return_for_correction_remains_in_work_new_round_resets_acceptance(self):
        task = self.task('return')
        service.discussion_action(self.worker, task, 'resolve', 'Результат')
        service.discussion_action(self.author, task, 'reopen', 'Исправить')
        task.refresh_from_db(); self.assertIsNotNone(task.started_at)
        self.assertIn(task.pk, self.ids(self.page(status='in_progress')))
        service.discussion_action(self.worker, task, 'resolve', 'Исправлено')
        service.discussion_action(self.author, task, 'accept')
        task.refresh_from_db()
        with self.assertRaises(ValidationError): service.take(self.worker, task)
        service.state(task, 'OPEN')
        task.refresh_from_db(); self.assertIsNone(task.started_at)

    def test_admin_filtered_closure_uses_same_stage_and_direction(self):
        new = self.task('admin-new', created_by=self.admin)
        started = self.task('admin-started', created_by=self.admin); service.take(self.worker, started)
        self.task('not-authored')
        params = {'scope': 'outgoing', 'status': 'in_progress', 'kind': 'MANUAL'}
        self.assertEqual(set(filtered(self.admin, params).values_list('pk', flat=True)), {started.pk})
        params['status'] = 'not_taken'
        self.assertEqual(set(filtered(self.admin, params).values_list('pk', flat=True)), {new.pk})
        params['status'] = 'history'
        self.assertFalse(filtered(self.admin, params).exists())
        params['status'] = 'invalid'
        self.assertFalse(filtered(self.admin, params).exists())
        self.assertEqual(len(self.ids(self.page(self.admin))), 3)
        self.assertContains(self.page(self.admin, scope='all'), 'Все задания компании')

    def test_pagination_links_keep_direction_kind_and_stage(self):
        for number in range(51): self.task(f'page-{number}', status='DONE')
        page = self.page(self.author, scope='outgoing', status='history', kind='MANUAL')
        self.assertTrue(page.context['page'].has_next())
        self.assertContains(page, '?scope=outgoing&status=history&kind=MANUAL&page=2')
        self.assertEqual(len(self.ids(self.page(self.author, scope='outgoing', status='history', kind='MANUAL', page=2))), 1)

    def test_company_isolation_for_lists_and_acceptance(self):
        task = self.task('private')
        foreign_company = Company.objects.create(name='Foreign queue')
        foreign = User.objects.create_user(username='queue-foreign', company=foreign_company, role=Role.objects.get(code='ADMIN'))
        self.assertFalse(self.ids(self.page(foreign, scope='all', status='all')))
        self.assertEqual(self.client.post(reverse('planning:task_detail', args=[task.pk]), {'action': 'take'}).status_code, 404)

    def test_migration_restores_real_acceptance_but_not_previous_closed_round(self):
        old = timezone.now() - timedelta(days=3)
        current = self.task('migrate-current')
        stale = self.task('migrate-stale')
        untouched = self.task('migrate-untouched')
        for task in [current, stale]:
            event = WorkTaskEvent.objects.create(company=self.company, task=task, action='Принято в работу')
            WorkTaskEvent.objects.filter(pk=event.pk).update(created_at=old)
        WorkTaskEvent.objects.create(company=self.company, task=stale, action='Выполнено')
        migration = import_module('apps.planning.migrations.0050_worktask_started_at')
        migration.restore_acceptances(apps, connection.schema_editor())
        current.refresh_from_db(); stale.refresh_from_db(); untouched.refresh_from_db()
        self.assertEqual(current.started_at, old)
        self.assertIsNone(stale.started_at)
        self.assertIsNone(untouched.started_at)


@skipUnless(os.environ.get('TASK_QUEUE_BROWSER_TESTS') == '1', 'Optional task queue browser scenario')
class TaskQueueBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_take_tabs_filters_and_mobile(self):
        TaskQueueTests.setUpTestData.__func__(type(self))
        for key, title, author, assignee, status in [
            ('browser-in', 'Входящее поручение', self.author, self.worker, 'OPEN'),
            ('browser-out', 'Исходящее поручение', self.worker, self.author, 'OPEN'),
            ('browser-history', 'Прошлое поручение', self.worker, self.author, 'DONE'),
        ]:
            service.ensure(self.company, key, kind='MANUAL', title=title, created_by=author, assigned_to=assignee, status=status)
        self.client.force_login(self.worker)
        config = {'base': self.live_server_url, 'url': reverse('planning:task_list'), 'cookie': self.client.cookies['sessionid'].value, 'chromium': shutil.which('chromium')}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/task_queues.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, env=dict(os.environ, TASK_QUEUE_CONFIG=json.dumps(config)), timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + '\n' + result.stderr)
        print(result.stdout.strip())
