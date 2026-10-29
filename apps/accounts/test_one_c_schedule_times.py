from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from .models import OneCConnection, OneCDiagnosticRun
from .one_c_schedules import first_launch, scheduled_deadline
from . import test_one_c_catalogue_jobs as catalogue_tests


def utc(value):
    return datetime.fromisoformat(value).replace(tzinfo=dt_timezone.utc)


@override_settings(TIME_ZONE='Europe/Moscow')
class ScheduleClockTests(SimpleTestCase):
    def deadline(self, frequency, now, previous=None, weekday=0, interval=1440):
        connection = OneCConnection(employees_interval_minutes=interval, employees_schedule={
            'frequency': frequency, 'time': '07:30', 'weekday': weekday,
            'starts_at': '2026-10-19T07:30:00+03:00'})
        run = OneCDiagnosticRun(created_at=previous) if previous else None
        return scheduled_deadline(connection, 'employees', now, run)

    def test_daily_uses_moscow_clock_and_manual_run_does_not_shift_it(self):
        self.assertEqual(self.deadline('daily', utc('2026-10-29T04:29')), utc('2026-10-28T04:30'))
        self.assertEqual(self.deadline('daily', utc('2026-10-29T04:29'), utc('2026-10-28T05:00')), utc('2026-10-29T04:30'))
        self.assertEqual(self.deadline('daily', utc('2026-10-29T04:30'), utc('2026-10-29T04:00')), utc('2026-10-29T04:30'))
        self.assertEqual(self.deadline('daily', utc('2026-10-29T05:00'), utc('2026-10-29T04:35')), utc('2026-10-30T04:30'))

    def test_weekly_boundaries_and_month_rollover(self):
        # Monday's slot remains due after downtime until one run covers it.
        self.assertEqual(self.deadline('weekly', utc('2026-11-01T23:00'), weekday=0), utc('2026-10-26T04:30'))
        self.assertEqual(self.deadline('weekly', utc('2026-11-01T23:00'), utc('2026-10-26T05:00')), utc('2026-11-02T04:30'))
        self.assertEqual(self.deadline('weekly', utc('2026-11-02T04:30'), utc('2026-10-26T05:00')), utc('2026-11-02T04:30'))

    def test_interval_keeps_anchor_when_run_was_delayed(self):
        now = utc('2026-10-29T06:35')
        self.assertEqual(self.deadline('interval', now, utc('2026-10-29T05:40'), interval=60), utc('2026-10-29T06:30'))
        self.assertEqual(self.deadline('interval', now, utc('2026-10-29T06:32'), interval=60), utc('2026-10-29T07:30'))

    def test_first_start_is_future_and_no_job_runs_before_it(self):
        from datetime import time
        self.assertEqual(first_launch('daily', time(7, 30), 0, utc('2026-10-29T05:00')), utc('2026-10-30T04:30'))
        self.assertEqual(first_launch('weekly', time(7, 30), 0, utc('2026-10-29T05:00')), utc('2026-11-02T04:30'))
        self.assertEqual(self.deadline('daily', utc('2026-10-18T04:30')), utc('2026-10-19T04:30'))


@override_settings(TIME_ZONE='Europe/Moscow')
class ClockScheduleJobTests(TestCase):
    setUp = catalogue_tests.CatalogueJobTests.setUp
    finish = catalogue_tests.CatalogueJobTests.finish
    def test_save_daily_weekly_and_disable_independently(self):
        now = utc('2026-10-29T04:00')
        with patch('apps.accounts.one_c_jobs.timezone.now', return_value=now):
            self.assertRedirects(self.client.post(self.url, {'action': 'employees_schedule',
                'frequency': 'daily', 'time': '07:30'}), self.url)
            self.assertRedirects(self.client.post(self.url, {'action': 'positions_schedule',
                'frequency': 'weekly', 'time': '08:15', 'weekday': '4'}), self.url)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.employees_schedule['starts_at'], '2026-10-29T07:30:00+03:00')
        self.assertEqual(self.connection.positions_schedule['weekday'], 4)
        self.assertEqual(self.connection.positions_schedule['starts_at'], '2026-10-30T08:15:00+03:00')
        self.assertEqual(self.connection.positions_interval_minutes, 10080)
        page = self.client.get(self.url)
        self.assertContains(page, 'Ежедневно в 07:30')
        self.assertContains(page, 'пятница · 08:15')
        self.assertContains(page, 'Europe/Moscow')
        self.client.post(self.url, {'action': 'employees_schedule', 'frequency': 'manual'})
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.employees_interval_minutes, 0)
        self.assertEqual(self.connection.employees_schedule, {})
        self.assertEqual(self.connection.positions_schedule['weekday'], 4)
        from apps.audit.models import AuditLog
        self.assertTrue(AuditLog.objects.filter(user=self.admin, new_value__positions_schedule__time='08:15').exists())

    def test_invalid_calendar_fields_do_not_change_saved_configuration(self):
        payload = {'action': 'employees_schedule', 'frequency': 'daily', 'time': '07:30'}
        self.client.post(self.url, payload)
        self.connection.refresh_from_db()
        saved = self.connection.employees_schedule
        for invalid in [dict(payload, time=''), dict(payload, time='25:70'),
                        dict(payload, frequency='weekly', weekday='9'),
                        dict(payload, frequency='interval', interval='5')]:
            response = self.client.post(self.url, invalid)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['import_jobs'][0]['form'].errors)
            self.connection.refresh_from_db()
            self.assertEqual(self.connection.employees_schedule, saved)

    def test_same_settings_preserve_anchor_and_interval_change_reanchors(self):
        payload = {'action': 'employees_schedule', 'frequency': 'interval', 'interval': '60', 'time': '07:30'}
        with patch('apps.accounts.one_c_jobs.timezone.now', return_value=utc('2026-10-29T04:00')):
            self.client.post(self.url, payload)
        self.connection.refresh_from_db()
        start = self.connection.employees_schedule['starts_at']
        with patch('apps.accounts.one_c_jobs.timezone.now', return_value=utc('2026-10-30T04:00')):
            self.client.post(self.url, payload)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.employees_schedule['starts_at'], start)
        with patch('apps.accounts.one_c_jobs.timezone.now', return_value=utc('2026-10-30T04:00')):
            self.client.post(self.url, dict(payload, interval='120'))
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.employees_schedule['starts_at'], '2026-10-30T07:30:00+03:00')

    def test_worker_waits_for_time_catches_up_once_and_keeps_shared_queue(self):
        now = utc('2026-10-29T04:00')
        with patch('apps.accounts.one_c_jobs.timezone.now', return_value=now):
            for kind in ['employees', 'positions']:
                self.client.post(self.url, {'action': kind + '_schedule', 'frequency': 'daily', 'time': '07:30'})
        from .one_c_schedules import enqueue_due_imports
        enqueue_due_imports(now)
        self.assertFalse(self.connection.runs.filter(status='QUEUED').exists())
        later = now + timedelta(days=3, minutes=40)
        with patch('apps.accounts.one_c.timezone.now', return_value=later):
            enqueue_due_imports(later)
            first = self.connection.runs.get(status='QUEUED')
            OneCDiagnosticRun.objects.filter(pk=first.pk).update(created_at=later)
            enqueue_due_imports(later)
            self.assertEqual(self.connection.runs.filter(status='QUEUED').count(), 1)
            self.finish(first)
            enqueue_due_imports(later)
            second = self.connection.runs.get(status='QUEUED')
            self.assertNotEqual(first.report, second.report)
            OneCDiagnosticRun.objects.filter(pk=second.pk).update(created_at=later, status='ERROR')
            OneCConnection.objects.filter(pk=self.connection.pk).update(active_until=None)
            enqueue_due_imports(later)
        self.assertFalse(self.connection.runs.filter(status='QUEUED').exists())

    def test_unconfirmed_source_blocks_new_schedule_but_allows_disabling(self):
        self.connection.database = 'changed'
        self.connection.save()
        response = self.client.post(self.url, {'action': 'employees_schedule', 'frequency': 'daily', 'time': '07:30'})
        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.employees_schedule, {})
        self.assertRedirects(self.client.post(self.url, {'action': 'employees_schedule', 'frequency': 'manual'}), self.url)
