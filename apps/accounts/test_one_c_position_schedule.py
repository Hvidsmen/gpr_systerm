from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Company,Role,User,OneCConnection,OneCDiagnosticRun
from .one_c_positions import source_confirmed,enqueue_due_positions


class PositionScheduleTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Scheduled import')
        role,_=Role.objects.get_or_create(code='ADMIN',defaults={'name':'Admin'})
        self.user=User.objects.create_user(username='schedule-admin',company=self.company,role=role)
        self.client.force_login(self.user)
        self.connection=OneCConnection.objects.create(company=self.company)
        self.url=reverse('accounts:one_c_jobs')
        from .test_one_c_positions import staffing_diagnostic
        self.report=staffing_diagnostic()
        self.diagnostic=OneCDiagnosticRun.objects.create(connection=self.connection,server=self.connection.server,database=self.connection.database,status='DONE',report=self.report)

    def test_manual_launch_and_duplicate_prevention(self):
        self.assertContains(self.client.get(self.url),'Запустить сейчас')
        self.assertRedirects(self.client.post(self.url,{'action':'positions_run'}),self.url)
        run=OneCDiagnosticRun.objects.get(report__positions_import=True)
        self.assertEqual(run.created_by,self.user)
        self.client.post(self.url,{'action':'positions_run'})
        self.assertEqual(OneCDiagnosticRun.objects.filter(report__positions_import=True).count(),1)

    def test_save_disable_and_invalid_schedule(self):
        self.assertRedirects(self.client.post(self.url,{'action':'positions_schedule','interval':1440}),self.url)
        self.connection.refresh_from_db();self.assertEqual(self.connection.positions_interval_minutes,1440)
        response=self.client.post(self.url,{'action':'positions_schedule','interval':5})
        self.assertContains(response,'Минимальный интервал')
        self.connection.refresh_from_db();self.assertEqual(self.connection.positions_interval_minutes,1440)
        self.client.post(self.url,{'action':'positions_schedule','interval':0})
        self.connection.refresh_from_db();self.assertEqual(self.connection.positions_interval_minutes,0)

    def test_worker_enqueues_due_import_and_respects_interval(self):
        self.connection.positions_interval_minutes=60;self.connection.save()
        enqueue_due_positions(timezone.now())
        run=OneCDiagnosticRun.objects.get(report__positions_import=True)
        self.assertEqual(run.status,'QUEUED');self.assertIsNone(run.created_by)
        enqueue_due_positions(timezone.now())
        self.assertEqual(OneCDiagnosticRun.objects.filter(report__positions_import=True).count(),1)
        OneCConnection.objects.filter(pk=self.connection.pk).update(active_until=None)
        OneCDiagnosticRun.objects.filter(pk=run.pk).update(status='DONE')
        enqueue_due_positions(timezone.now())
        self.assertEqual(OneCDiagnosticRun.objects.filter(report__positions_import=True).count(),1)
        OneCDiagnosticRun.objects.filter(pk=run.pk).update(created_at=timezone.now()-timedelta(hours=2))
        enqueue_due_positions(timezone.now())
        self.assertEqual(OneCDiagnosticRun.objects.filter(report__positions_import=True).count(),2)

    def test_changed_source_blocks_manual_and_scheduled_import(self):
        self.connection.database='unconfirmed';self.connection.positions_interval_minutes=60;self.connection.save()
        self.assertFalse(source_confirmed(self.connection))
        self.client.post(self.url,{'action':'positions_run'})
        self.client.post(self.url,{'action':'positions_schedule','interval':30})
        enqueue_due_positions(timezone.now())
        self.assertFalse(OneCDiagnosticRun.objects.filter(report__positions_import=True).exists())
        self.connection.refresh_from_db();self.assertEqual(self.connection.positions_interval_minutes,60)
        self.client.post(self.url,{'action':'positions_schedule','interval':0})
        self.connection.refresh_from_db();self.assertEqual(self.connection.positions_interval_minutes,0)
