import json
import tempfile
from datetime import timedelta
from pathlib import PosixPath
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Company, OneCConnection, OneCDiagnosticRun, Role, User
from .one_c import process_queue
from .uat_sources import PARTS, REPAIR_SHEET, WAYBILL


SCHEMA = [
    {'schema':'dbo','table':'_Document123','column':'_IDRRef','type':'binary','max_length':16,'is_nullable':False},
    {'schema':'dbo','table':'_Document123','column':'_Number','type':'nvarchar','max_length':18,'is_nullable':False},
]
LABELS = {'dbo._Document123': {'name':'Документ.ПутевойЛист','columns':{'_Number':'Номер'}}}
UAT_SCHEMA = [
    {'schema': key.split('.')[0], 'table': key.split('.')[1], 'column': column,
     'type': 'binary', 'max_length': 16, 'is_nullable': False}
    for source in (WAYBILL, REPAIR_SHEET) for key in (source['key'],) for column in source['columns']
] + [
    {'schema': key.split('.')[0], 'table': key.split('.')[1], 'column': column,
     'type': 'binary', 'max_length': 16, 'is_nullable': False}
    for key, _label, columns in PARTS for column in columns
]
UAT_LABELS = {
    WAYBILL['key']: {'name': WAYBILL['name'], 'columns': {}},
    REPAIR_SHEET['key']: {'name': REPAIR_SHEET['name'], 'columns': {}},
}


@override_settings(UAT_DATABASE='uat01_ivanov')
class UatDiscoveryTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='UAT discovery')
        role,_=Role.objects.get_or_create(code='ADMIN',defaults={'name':'Admin'})
        self.admin=User.objects.create_user(username='uat-admin',company=self.company,role=role)
        self.connection=OneCConnection.objects.create(company=self.company,server='shared-sql',database='zup01')
        self.client.force_login(self.admin)
        self.url=reverse('accounts:uat')

    def test_uses_zup_server_and_shared_worker_identity_with_separate_database(self):
        response=self.client.get(self.url)
        self.assertContains(response,'shared-sql')
        self.assertContains(response,'uat01_ivanov')
        self.assertContains(response,'та же Windows-учётная запись')
        self.assertEqual(self.client.post(self.url,{'action':'diagnose'}).status_code,302)
        run=OneCDiagnosticRun.objects.get()
        self.assertEqual((run.server,run.database),('shared-sql','uat01_ivanov'))
        self.assertEqual(run.report,{'uat_discovery':True})
        self.assertTrue(run.trust_certificate)
        self.connection.refresh_from_db();self.assertEqual(self.connection.database,'zup01')

    @patch('apps.accounts.one_c.subprocess.run')
    def test_worker_marks_uat_report_and_passes_discovery_switch(self,execute):
        self.client.post(self.url,{'action':'diagnose'})
        run=OneCDiagnosticRun.objects.get()
        with tempfile.TemporaryDirectory() as directory:
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            target.mkdir(parents=True)
            (target/'status.json').write_text(json.dumps({'ok':True}))
            (target/'report.json').write_text(json.dumps({'schema':SCHEMA,'metadata_labels':LABELS}))
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.os.name','nt'),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True):
                process_queue()
        run.refresh_from_db();self.assertEqual(run.status,'DONE',run.error)
        self.assertTrue(run.report['uat_discovery'])
        command=execute.call_args.args[0]
        self.assertIn('-UatDiscovery',command)
        self.assertIn('-TrustServerCertificate',command)
        self.assertEqual(command[command.index('-Database')+1],'uat01_ivanov')
        page=self.client.get(self.url)
        self.assertContains(page,'Документ.ПутевойЛист')
        self.assertContains(page,'dbo._Document123')

    @patch('apps.accounts.one_c.subprocess.run')
    def test_uat_only_worker_leaves_other_1c_jobs_queued(self,execute):
        self.client.post(self.url,{'action':'diagnose'})
        uat=OneCDiagnosticRun.objects.get()
        other=OneCDiagnosticRun.objects.create(connection=self.connection,server='shared-sql',database='zup01')
        self.connection.active_until=timezone.now()-timedelta(minutes=1)
        self.connection.save(update_fields=['active_until'])
        with tempfile.TemporaryDirectory() as directory:
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/uat.pk.hex
            target.mkdir(parents=True)
            (target/'status.json').write_text(json.dumps({'ok':True}))
            (target/'report.json').write_text(json.dumps({'schema':SCHEMA,'metadata_labels':LABELS}))
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.os.name','nt'),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True):
                process_queue(uat_only=True)
        uat.refresh_from_db();other.refresh_from_db()
        self.assertEqual(uat.status,'DONE')
        self.assertEqual(other.status,'QUEUED')

    @patch('apps.accounts.one_c.subprocess.run')
    def test_confirmed_structure_can_load_waybills_and_repair_sheets(self,execute):
        OneCDiagnosticRun.objects.create(connection=self.connection,server='shared-sql',database='uat01_ivanov',
            status='DONE',report={'uat_discovery':True,'schema':UAT_SCHEMA,'metadata_labels':UAT_LABELS})
        page=self.client.get(self.url)
        self.assertContains(page,'Структура подходит')
        response=self.client.post(self.url,{'action':'import','from_date':'2026-10-01'})
        self.assertEqual(response.status_code,302)
        run=OneCDiagnosticRun.objects.filter(report__uat_import=True).get()
        self.assertEqual(run.report['from_date'],'2026-10-01')
        with tempfile.TemporaryDirectory() as directory:
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            target.mkdir(parents=True)
            (target/'status.json').write_text(json.dumps({'ok':True}))
            (target/'report.json').write_text(json.dumps({'uat_import':True,'from_date':'2026-10-01',
                'waybill_count':1,'repair_sheet_count':1,
                'source_stats':[{'source':'waybills','total_rows':10,'active_rows':9,'period_rows':2,'posted_period_rows':1,'first_date':'2024-01-01','last_date':'2026-11-20'}],
                'waybills':[{'number':'PL-1','vehicle':'KAMAZ'}],
                'repair_sheets':[{'number':'RL-1','vehicle':'GAZ'}]}))
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.os.name','nt'),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True):
                process_queue(uat_only=True)
        run.refresh_from_db();self.assertEqual(run.status,'DONE',run.error)
        command=execute.call_args.args[0]
        self.assertIn('-UatImport',command)
        self.assertEqual(command[command.index('-UatFromDate')+1],'2026-10-01')
        page=self.client.get(self.url)
        self.assertContains(page,'PL-1');self.assertContains(page,'RL-1')
        self.assertContains(page,'Проверка исходных таблиц УАТ')

    @patch('apps.accounts.uat.subprocess.Popen')
    def test_staging_click_starts_dedicated_worker_and_can_retry_queued_job(self,spawn):
        with tempfile.TemporaryDirectory() as directory,override_settings(
                CONFIG_DIR=directory,STAGING_CONFIG={'revision':'a'*40}):
            response=self.client.post(self.url,{'action':'diagnose'})
            self.assertEqual(response.status_code,302)
            command=spawn.call_args.args[0]
            self.assertEqual(command[-2:],['process_1c','--uat-only'])
            self.assertTrue((PosixPath(directory)/'.runtime/1c-worker.log').exists())
            page=self.client.get(self.url)
            self.assertContains(page,'Запустить ожидающую диагностику')
            self.client.post(self.url,{'action':'run_pending'})
            self.assertEqual(spawn.call_count,2)

    def test_staging_uat_command_publishes_owned_process_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=PosixPath(directory)/'.runtime/uat-worker.json'
            def inspect_worker(**_kwargs):
                self.assertEqual(json.loads(marker.read_text())['pid'],__import__('os').getpid())
                return 0
            with override_settings(CONFIG_DIR=directory,STAGING_MODE=True),\
                    patch('apps.accounts.management.commands.process_1c.process_queue',side_effect=inspect_worker):
                call_command('process_1c','--uat-only')
            self.assertFalse(marker.exists())

    @patch('apps.accounts.uat.subprocess.Popen')
    def test_uat_cannot_start_during_test_data_refresh(self,spawn):
        with tempfile.TemporaryDirectory() as directory,override_settings(
                CONFIG_DIR=directory,STAGING_CONFIG={'revision':'a'*40}):
            runtime=PosixPath(directory)/'.runtime';runtime.mkdir()
            (runtime/'staging-update.json').write_text(json.dumps({'state':'copying'}),encoding='utf-8')
            response=self.client.post(self.url,{'action':'diagnose'},follow=True)
            self.assertContains(response,'Сначала дождитесь завершения обновления')
            spawn.assert_not_called()

    def test_progress_endpoint_reports_real_powershell_checkpoint(self):
        run=OneCDiagnosticRun.objects.create(connection=self.connection,server='shared-sql',
            database='uat01_ivanov',status='RUNNING',report={'uat_discovery':True})
        with tempfile.TemporaryDirectory() as directory,override_settings(CONFIG_DIR=directory):
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            target.mkdir(parents=True)
            (target/'progress.json').write_text(json.dumps({'stage':'metadata'}),encoding='utf-8')
            response=self.client.get(reverse('accounts:uat_status'))
            self.assertEqual(response.status_code,200)
            payload=response.json()
            self.assertEqual(payload['stage_number'],5)
            self.assertEqual(payload['stage'],'Расшифровка метаданных')
            self.assertTrue(payload['busy'])
            page=self.client.get(self.url)
            self.assertContains(page,'Этап 5 из 6')
            self.assertContains(page,'data-uat-progress')

    def test_download_selects_only_uat_structure(self):
        OneCDiagnosticRun.objects.create(connection=self.connection,server='shared-sql',database='zup01',status='DONE',report={'schema':[]})
        run=OneCDiagnosticRun.objects.create(connection=self.connection,server='shared-sql',database='uat01_ivanov',status='DONE',report={'uat_discovery':True,'schema':SCHEMA,'metadata_labels':LABELS})
        response=self.client.get(reverse('accounts:one_c_report'),{'uat':'1'})
        self.assertEqual(response.status_code,200)
        self.assertIn(f'uat-structure-{run.pk.hex}.json',response['Content-Disposition'])
        payload=json.loads(response.content)
        self.assertEqual(payload['diagnostic']['database'],'uat01_ivanov')
        self.assertEqual(payload['tables'][0]['name'],'Документ.ПутевойЛист')

    def test_non_admin_cannot_access(self):
        planner,_=Role.objects.get_or_create(code='PLANNER',defaults={'name':'Planner'})
        self.admin.role=planner;self.admin.save(update_fields=['role'])
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_script_keeps_integrated_security_and_uat_terms(self):
        script=(PosixPath(__file__).resolve().parents[2]/'ops/diagnose-1c.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("$builder['Integrated Security']=$true",script)
        self.assertIn('[switch]$UatDiscovery',script)
        self.assertIn('[switch]$UatImport',script)
        self.assertIn('[dbo].[_Document173]',script)
        self.assertIn('[dbo].[_Document178]',script)
        self.assertIn("Write-ProgressState 'connecting'",script)
        self.assertIn("Write-ProgressState 'metadata'",script)
        self.assertIn("'путев'",script)
        self.assertIn("'ремонт'",script)

    def test_uat_import_reports_source_counts_and_keeps_posted_filter(self):
        script=(PosixPath(__file__).resolve().parents[2]/'ops/diagnose-1c.ps1').read_text(encoding='utf-8-sig')
        import_block=script.rsplit('if($UatImport){',1)[1].split('if($EmployeesImport){',1)[0]
        self.assertEqual(import_block.count("WHERE p.[_Marked]=0x00 AND p.[_Posted]=0x01 AND p.[_Date_Time]>=@storedFrom"),7)
        self.assertIn("CASE WHEN p.[_Posted]=0x01 THEN 1 ELSE 0 END AS posted",import_block)
        self.assertIn("source_stats=$sourceStats",import_block)
        self.assertIn("AS posted_period_rows",import_block)
