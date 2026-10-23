from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import Company,Role,User,OneCConnection,OneCDiagnosticRun
from .one_c import ConnectionForm,enqueue,process_queue

class OneCTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='1C company')
        role,_=Role.objects.get_or_create(code='ADMIN',defaults={'name':'Admin'})
        self.admin=User.objects.create_user(username='one-c-admin',company=self.company,role=role)
        self.client.force_login(self.admin)
        self.connection=OneCConnection.objects.create(company=self.company)
        self.url=reverse('accounts:one_c')

    def test_access_and_company_isolation(self):
        other=OneCConnection.objects.create(company=Company.objects.create(name='Other'))
        OneCDiagnosticRun.objects.create(connection=other,server='SECRET-SERVER',database='zup')
        self.assertNotContains(self.client.get(self.url),'SECRET-SERVER')
        role,_=Role.objects.get_or_create(code='PLANNER',defaults={'name':'Planner'})
        self.admin.role=role;self.admin.save()
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url,{'action':'diagnose'}).status_code,403)

    def test_enqueue_is_idempotent_and_captures_settings(self):
        self.client.post(self.url,{'action':'diagnose'})
        self.client.post(self.url,{'action':'diagnose'})
        self.assertEqual(OneCDiagnosticRun.objects.count(),1)
        run=OneCDiagnosticRun.objects.get()
        self.assertEqual(run.created_by,self.admin)
        self.assertEqual(run.server,'msk-db')
        self.assertContains(self.client.get(self.url),'Ожидает')

    def test_validation_and_settings_do_not_release_claim(self):
        enqueue(self.connection)
        self.client.post(self.url,{'action':'save','server':'sql-server','database':'zup','diagnostic_interval_minutes':'30'})
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.server,'sql-server')
        self.assertIsNotNone(self.connection.active_until)
        self.assertIsNone(enqueue(self.connection))
        for server in ['bad" -Command x','x;whoami','https://sql']:
            form=ConnectionForm({'server':server,'database':'zup','diagnostic_interval_minutes':0})
            self.assertFalse(form.is_valid())
        self.assertFalse(ConnectionForm({'server':'msk-db','database':'zup','diagnostic_interval_minutes':1}).is_valid())

    @patch('apps.accounts.one_c.os.name','posix')
    def test_worker_records_failure_and_releases_lease(self):
        run=enqueue(self.connection)
        process_queue()
        run.refresh_from_db();self.connection.refresh_from_db()
        self.assertEqual(run.status,'ERROR');self.assertIsNotNone(run.finished_at)
        self.assertIn('Windows',run.error)
        self.assertIsNone(self.connection.active_until)

    @patch('apps.accounts.one_c.os.name','posix')
    def test_schedule_and_stale_recovery(self):
        self.connection.diagnostic_interval_minutes=30;self.connection.save()
        process_queue();process_queue()
        self.assertEqual(self.connection.runs.count(),1)
        old=self.connection.runs.get()
        OneCDiagnosticRun.objects.filter(pk=old.pk).update(created_at=timezone.now()-timedelta(hours=1))
        process_queue()
        self.assertEqual(self.connection.runs.count(),2)
        run=enqueue(self.connection)
        OneCConnection.objects.filter(pk=self.connection.pk).update(active_until=timezone.now()-timedelta(minutes=1))
        process_queue();run.refresh_from_db()
        self.assertEqual(run.status,'ERROR');self.assertIn('прерван',run.error)

    @patch('apps.accounts.one_c.os.name','nt')
    @patch('apps.accounts.one_c.subprocess.run')
    def test_windows_command_no_shell_and_report_render(self,execute):
        # Mock Windows path handling separately from OS checks on the Linux test host.
        import tempfile,json
        from pathlib import PosixPath
        from django.test import override_settings
        with tempfile.TemporaryDirectory() as directory:
            run=enqueue(self.connection)
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            target.mkdir(parents=True)
            (target/'status.json').write_text(json.dumps({'ok':True}))
            (target/'report.json').write_text(json.dumps({'windows_identity':'DOMAIN\\HOST$','connection':{'login':'DOMAIN\\HOST$'},'reference_tables_checked':1,'candidates':[{'schema':'dbo','table':'_Reference123','matched_rows':4,'sampled_rows':5}]}))
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True):process_queue()
            run.refresh_from_db();self.assertEqual(run.status,'DONE',run.error)
            args=execute.call_args.args[0]
            self.assertIn('-RunHere',args);self.assertNotIn('shell',execute.call_args.kwargs)
            self.assertContains(self.client.get(self.url),'_Reference123')
