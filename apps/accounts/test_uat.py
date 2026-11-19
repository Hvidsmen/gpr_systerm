import json
import tempfile
from pathlib import PosixPath
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Company, OneCConnection, OneCDiagnosticRun, Role, User
from .one_c import process_queue


SCHEMA = [
    {'schema':'dbo','table':'_Document123','column':'_IDRRef','type':'binary','max_length':16,'is_nullable':False},
    {'schema':'dbo','table':'_Document123','column':'_Number','type':'nvarchar','max_length':18,'is_nullable':False},
]
LABELS = {'dbo._Document123': {'name':'Документ.ПутевойЛист','columns':{'_Number':'Номер'}}}


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
        self.connection.refresh_from_db();self.assertEqual(self.connection.database,'zup01')

    @patch('apps.accounts.one_c.os.name','nt')
    @patch('apps.accounts.one_c.subprocess.run')
    def test_worker_marks_uat_report_and_passes_discovery_switch(self,execute):
        self.client.post(self.url,{'action':'diagnose'})
        run=OneCDiagnosticRun.objects.get()
        with tempfile.TemporaryDirectory() as directory:
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/run.pk.hex
            target.mkdir(parents=True)
            (target/'status.json').write_text(json.dumps({'ok':True}))
            (target/'report.json').write_text(json.dumps({'schema':SCHEMA,'metadata_labels':LABELS}))
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True):
                process_queue()
        run.refresh_from_db();self.assertEqual(run.status,'DONE',run.error)
        self.assertTrue(run.report['uat_discovery'])
        command=execute.call_args.args[0]
        self.assertIn('-UatDiscovery',command)
        self.assertEqual(command[command.index('-Database')+1],'uat01_ivanov')
        page=self.client.get(self.url)
        self.assertContains(page,'Документ.ПутевойЛист')
        self.assertContains(page,'dbo._Document123')

    @patch('apps.accounts.one_c.os.name','nt')
    @patch('apps.accounts.one_c.subprocess.run')
    def test_uat_only_worker_leaves_other_1c_jobs_queued(self,execute):
        self.client.post(self.url,{'action':'diagnose'})
        uat=OneCDiagnosticRun.objects.get()
        other=OneCDiagnosticRun.objects.create(connection=self.connection,server='shared-sql',database='zup01')
        with tempfile.TemporaryDirectory() as directory:
            target=PosixPath(directory)/'.runtime/1c-diagnostics'/uat.pk.hex
            target.mkdir(parents=True)
            (target/'status.json').write_text(json.dumps({'ok':True}))
            (target/'report.json').write_text(json.dumps({'schema':SCHEMA,'metadata_labels':LABELS}))
            with override_settings(CONFIG_DIR=directory),patch('apps.accounts.one_c.Path',PosixPath),patch('apps.accounts.one_c.subprocess.CREATE_NO_WINDOW',0,create=True):
                process_queue(uat_only=True)
        uat.refresh_from_db();other.refresh_from_db()
        self.assertEqual(uat.status,'DONE')
        self.assertEqual(other.status,'QUEUED')

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

    def test_staging_page_reports_old_supervisor_until_uat_worker_is_ready(self):
        with tempfile.TemporaryDirectory() as directory,override_settings(
                CONFIG_DIR=directory,STAGING_CONFIG={'revision':'a'*40}):
            self.assertContains(self.client.get(self.url),'перезапустите задание')
            runtime=PosixPath(directory)/'.runtime';runtime.mkdir()
            (runtime/'staging-supervisor.json').write_text(json.dumps(
                {'revision':'a'*40,'features':['uat-discovery']}),encoding='utf-8')
            self.assertNotContains(self.client.get(self.url),'перезапустите задание')

    def test_script_keeps_integrated_security_and_uat_terms(self):
        script=(PosixPath(__file__).resolve().parents[2]/'ops/diagnose-1c.ps1').read_text(encoding='utf-8-sig')
        self.assertIn("$builder['Integrated Security']=$true",script)
        self.assertIn('[switch]$UatDiscovery',script)
        self.assertIn("'путев'",script)
        self.assertIn("'ремонт'",script)
