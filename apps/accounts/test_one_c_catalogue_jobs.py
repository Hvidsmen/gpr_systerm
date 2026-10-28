import json, os, shutil, subprocess
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Company, Role, User, OneCConnection, OneCDiagnosticRun
from .one_c_schedules import enqueue_due_imports, source_ready
from .test_one_c_employees import diagnostic


def both_sources():
    report = diagnostic()
    from .test_one_c_positions import staffing_diagnostic
    staffing=staffing_diagnostic()
    report['schema'].extend(staffing['schema'])
    for key,label in staffing['metadata_labels'].items():
        previous=report['metadata_labels'].get(key,{})
        report['metadata_labels'][key]={**previous,**label,'columns':{**previous.get('columns',{}),**label['columns']}}
    return report


class CatalogueJobTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Catalogue schedules')
        self.admin = User.objects.create_user(username='catalogue-job-admin', company=self.company, role=Role.objects.get(code='ADMIN'))
        self.connection = OneCConnection.objects.create(company=self.company, server='sql', database='zup')
        self.diagnostic = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='DONE', report=both_sources())
        self.client.force_login(self.admin)
        self.url = reverse('accounts:one_c_jobs')

    def finish(self, run):
        OneCDiagnosticRun.objects.filter(pk=run.pk).update(status='DONE')
        OneCConnection.objects.filter(pk=self.connection.pk).update(active_until=None)

    def test_two_cards_with_independent_forms_and_history(self):
        page = self.client.get(self.url)
        for kind in ['employees', 'positions']:
            self.assertContains(page, f'data-import-job="{kind}"')
            self.assertContains(page, f'id="{kind}_interval"')
            self.assertContains(page, f'value="{kind}_run"')
            self.assertContains(page, f'href="?kind={kind}"')
        self.assertEqual([job['interval'] for job in page.context['import_jobs']], [0, 0])

    def test_employee_manual_run_uses_own_flag_actor_and_shared_claim(self):
        self.assertRedirects(self.client.post(self.url, {'action': 'employees_run'}), self.url)
        run = self.connection.runs.get(report__employees_import=True)
        self.assertEqual(run.created_by, self.admin)
        self.assertNotIn('positions_import', run.report)
        for action in ['employees_run', 'positions_run']:
            self.client.post(self.url, {'action': action})
        self.assertEqual(self.connection.runs.count(), 2)
        self.finish(run)
        self.client.post(self.url, {'action': 'positions_run'})
        self.assertEqual(self.connection.runs.filter(report__positions_import=True).count(), 1)

    def test_independent_schedules_validation_and_audit(self):
        for kind, interval in [('positions', 1440), ('employees', 60)]:
            self.assertRedirects(self.client.post(self.url, {'action': kind + '_schedule', 'interval': interval}), self.url)
        self.connection.refresh_from_db()
        self.assertEqual((self.connection.positions_interval_minutes, self.connection.employees_interval_minutes), (1440, 60))
        invalid = self.client.post(self.url, {'action': 'employees_schedule', 'interval': 5})
        self.assertContains(invalid, 'Минимальный интервал')
        jobs = {job['kind']: job for job in invalid.context['import_jobs']}
        self.assertTrue(jobs['employees']['form'].errors)
        self.assertFalse(jobs['positions']['form'].errors)
        self.connection.refresh_from_db(); self.assertEqual(self.connection.employees_interval_minutes, 60)
        self.client.post(self.url, {'action': 'employees_schedule', 'interval': 0})
        self.connection.refresh_from_db()
        self.assertEqual((self.connection.positions_interval_minutes, self.connection.employees_interval_minutes), (1440, 0))
        from apps.audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(user=self.admin, new_value={'employees_interval_minutes': 60}).exists())

    def test_confirmation_is_separate_and_changed_connection_pauses_both(self):
        report = both_sources()
        report['metadata_labels'].pop('dbo._Reference297')
        self.diagnostic.report = report; self.diagnostic.save()
        self.assertFalse(source_ready(self.connection, 'employees'))
        self.assertTrue(source_ready(self.connection, 'positions'))
        self.client.post(self.url, {'action': 'employees_run'})
        self.client.post(self.url, {'action': 'employees_schedule', 'interval': 60})
        self.assertFalse(self.connection.runs.filter(report__employees_import=True).exists())
        self.connection.refresh_from_db(); self.assertEqual(self.connection.employees_interval_minutes, 0)
        self.connection.database = 'unconfirmed'; self.connection.employees_interval_minutes = 60; self.connection.positions_interval_minutes = 30; self.connection.save()
        enqueue_due_imports(timezone.now())
        self.assertEqual(self.connection.runs.count(), 1)
        self.client.post(self.url, {'action': 'employees_schedule', 'interval': 0})
        self.connection.refresh_from_db(); self.assertEqual(self.connection.employees_interval_minutes, 0)

    def test_due_jobs_take_turns_and_do_not_duplicate(self):
        self.connection.employees_interval_minutes = 60; self.connection.positions_interval_minutes = 60; self.connection.save()
        now = timezone.now()
        enqueue_due_imports(now)
        first = self.connection.runs.get(status='QUEUED')
        self.assertEqual(first.report, {'employees_import': True})
        self.assertIsNone(first.created_by)
        enqueue_due_imports(now)
        self.assertEqual(self.connection.runs.filter(status='QUEUED').count(), 1)
        self.finish(first)
        enqueue_due_imports(now)
        second = self.connection.runs.get(status='QUEUED')
        self.assertEqual(second.report, {'positions_import': True})
        self.finish(second)
        enqueue_due_imports(now)
        self.assertFalse(self.connection.runs.filter(status='QUEUED').exists())
        # Both are now overdue; choose the oldest deadline, not a fixed kind.
        OneCDiagnosticRun.objects.filter(pk=first.pk).update(created_at=now-timedelta(hours=2))
        OneCDiagnosticRun.objects.filter(pk=second.pk).update(created_at=now-timedelta(hours=3))
        enqueue_due_imports(now)
        self.assertEqual(self.connection.runs.get(status='QUEUED').report, {'positions_import': True})

    def test_error_respects_own_interval_and_other_import_does_not_postpone_it(self):
        now = timezone.now()
        self.connection.employees_interval_minutes = 60; self.connection.save()
        error = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='ERROR', report={'employees_import': True}, error='SQL unavailable')
        enqueue_due_imports(now)
        self.assertFalse(self.connection.runs.filter(status='QUEUED').exists())
        OneCDiagnosticRun.objects.filter(pk=error.pk).update(created_at=now-timedelta(hours=2))
        OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='DONE', report={'positions_import': True})
        enqueue_due_imports(now)
        self.assertEqual(self.connection.runs.get(status='QUEUED').report, {'employees_import': True})

    def test_latest_result_is_independent_and_ignores_previous_source(self):
        employee = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='ERROR', report={'employees_import': True}, error='Employee import failed')
        position = OneCDiagnosticRun.objects.create(connection=self.connection, server='sql', database='zup', status='DONE', report={'positions_import': True})
        OneCDiagnosticRun.objects.create(connection=self.connection, server='previous', database='zup', status='DONE', report={'employees_import': True})
        page = self.client.get(self.url)
        jobs = {job['kind']: job for job in page.context['import_jobs']}
        self.assertEqual(jobs['employees']['last'], employee)
        self.assertEqual(jobs['positions']['last'], position)
        self.assertContains(page, 'Employee import failed')

    def test_actions_cannot_change_other_company_or_run_for_non_admin(self):
        other = OneCConnection.objects.create(company=Company.objects.create(name='Other catalogue'), employees_interval_minutes=1440)
        self.client.post(self.url, {'action': 'employees_schedule', 'interval': 60, 'connection': other.pk})
        other.refresh_from_db(); self.assertEqual(other.employees_interval_minutes, 1440)
        planner = User.objects.create_user(username='catalogue-job-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(planner)
        for action in ['employees_run', 'employees_schedule']:
            self.assertEqual(self.client.post(self.url, {'action': action, 'interval': 60}).status_code, 403)


@skipUnless(os.environ.get('ONEC_JOBS_BROWSER_TESTS') == '1', 'Optional 1C catalogue jobs browser scenario')
class CatalogueJobBrowserTests(StaticLiveServerTestCase):
    serialized_rollback = True

    def test_independent_schedule_forms_and_launch(self):
        CatalogueJobTests.setUp(self)
        config = {'base': self.live_server_url, 'url': self.url, 'cookie': self.client.cookies['sessionid'].value, 'chromium': shutil.which('chromium')}
        script = Path(__file__).resolve().parents[2] / 'tests/browser/one_c_catalogue_jobs.js'
        result = subprocess.run(['node', str(script)], capture_output=True, text=True, env=dict(os.environ, ONEC_JOBS_CONFIG=json.dumps(config)), timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
