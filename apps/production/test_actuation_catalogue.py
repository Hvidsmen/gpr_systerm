from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
from django.test import TestCase,override_settings
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from apps.accounts.models import User,Role,Company
from apps.planning.models import WorkTask
from apps.planning.task_services import visible
from . import test_production_entry as fixtures
from .models import DailyFact,ActuationReason,ActuationDocument
from .actuation_reasons import propose,decide,choices
from .dispatch_models import WorkFactClaim


class ActuationCatalogueTests(TestCase):
    setUpTestData=classmethod(fixtures.ProductionEntryTests.setUpTestData.__func__)
    payload=fixtures.ProductionEntryTests.payload
    row=fixtures.ProductionEntryTests.row
    def setUp(self):
        self.head=User.objects.create_user(username='production-head',company=self.company,role=Role.objects.get(code='PRODUCTION_HEAD'))
        self.author=self.users['DISPATCHER'];self.client.force_login(self.author)
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.setting=override_settings(ACTUATION_FILES_ROOT=Path(self.temp.name)/'private');self.setting.enable();self.addCleanup(self.setting.disable)
        self.scope={'construction_object':self.obj.pk,'date':str(self.day)}
        self.session=uuid4()
    def marked_fact(self,work=None):
        reason=propose(self.author,'Ветер')
        return DailyFact.objects.create(company=self.company,project_work=work or self.work,date=self.day,actual_quantity=0,actuation_type='DAY',actuation_reason=reason.name,actuation_reason_ids=[reason.pk]),reason
    def claim(self,fact,user=None,session=None):
        WorkFactClaim.objects.update_or_create(company=self.company,construction_object=self.obj,date=self.day,work=fact.project_work,work_item=fact.work_item,defaults={'dispatcher':user or self.author,'session_key':session or self.session,'expires_at':timezone.now()+timedelta(minutes=5)})
    def upload(self,facts,batch=None,files=None):
        return self.client.post(reverse('production:actuation_documents'),dict(self.scope,session=str(self.session),facts=[f.pk for f in facts],batch=str(batch or uuid4()),files=files or [SimpleUploadedFile('акт.pdf',b'%PDF-test',content_type='application/pdf'),SimpleUploadedFile('фото.png',b'PNG-example')]))

    def test_default_and_multiple_reasons_use_only_selected_works(self):
        available=choices(self.company)
        self.assertEqual(len(available),2)
        data=self.payload('DISPATCHER','works');a=self.row(data,self.work);b=self.row(data,self.second_work)
        data.update({a+'actuation_type':'DAY',a+'actuation_reasons':[key for key,label in available],a+'actual_quantity':'7',b+'actual_quantity':'3'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        fact=DailyFact.objects.get(project_work=self.work)
        self.assertEqual(len(fact.actuation_reason_ids),2);self.assertEqual(fact.actual_quantity,7)
        self.assertEqual(DailyFact.objects.get(project_work=self.second_work).actual_quantity,3)
        self.assertFalse(WorkTask.objects.filter(key__startswith='actuation-reason:').exists())

    def test_new_reason_in_day_screen_routes_one_task_to_head(self):
        url=reverse('production:actuation_reason_propose')
        response=self.client.post(url,dict(self.scope,name='  Проблемы   с подъездной дорогой  '));self.assertEqual(response.status_code,200)
        reason=ActuationReason.objects.get(pk=response.json()['reason']['id'])
        self.assertEqual(reason.status,'PENDING');self.assertEqual(reason.task.assigned_to,self.head)
        self.assertTrue(visible(self.head).filter(pk=reason.task_id).exists())
        second=self.client.post(url,dict(self.scope,name='ПРОБЛЕМЫ С ПОДЪЕЗДНОЙ ДОРОГОЙ'))
        self.assertEqual(second.json()['reason']['id'],reason.pk)
        self.assertEqual(WorkTask.objects.filter(key=f'actuation-reason:{reason.pk}').count(),1)
        self.client.force_login(self.head)
        self.assertContains(self.client.get(reverse('planning:task_detail',args=[reason.task_id])),reverse('production:actuation_reason_review',args=[reason.pk]))

    def test_reason_approval_and_stale_decision(self):
        fact,reason=self.marked_fact()
        decide(self.head,reason.pk,'approve',0)
        reason.refresh_from_db();fact.refresh_from_db()
        self.assertEqual(reason.status,'APPROVED');self.assertEqual(reason.task.status,'DONE')
        self.assertEqual(fact.actuation_reason_ids,[reason.pk])
        with self.assertRaises(ValidationError):decide(self.head,reason.pk,'reject',0)

    def test_replace_updates_all_facts_deduplicates_and_keeps_documents(self):
        fact,reason=self.marked_fact();self.claim(fact)
        self.assertEqual(self.upload([fact]).status_code,200)
        target=ActuationReason.objects.get(pk=choices(self.company)[0][0])
        fact.actuation_reason_ids=[reason.pk,target.pk];fact.save()
        second=DailyFact.objects.create(company=self.company,project_work=self.second_work,date=self.day,actuation_type='FULL_DAY',actuation_reason=reason.name,actuation_reason_ids=[reason.pk])
        decide(self.head,reason.pk,'replace',0,target.pk,'Использовать принятую формулировку')
        for record in [fact,second]:
            record.refresh_from_db();self.assertEqual(record.actuation_reason_ids,[target.pk]);self.assertEqual(record.actuation_reason,target.name)
        self.assertEqual(ActuationDocument.objects.count(),2)
        reason.refresh_from_db();self.assertEqual(reason.replacement_id,target.pk)
        self.assertTrue(reason.task.events.filter(action='Решение по причине актировки').exists())

    def test_reject_keeps_past_reason_and_removes_it_from_new_choices(self):
        fact,reason=self.marked_fact();decide(self.head,reason.pk,'reject',0)
        fact.refresh_from_db();self.assertEqual(fact.actuation_reason_ids,[reason.pk]);self.assertIn('удалена',fact.actuation_reason)
        self.assertNotIn(str(reason.pk),dict(choices(self.company)))
        self.assertIn(str(reason.pk),dict(choices(self.company,[reason.pk])))
        with self.assertRaises(ValidationError):propose(self.author,reason.name)
        with self.assertRaises(ValidationError):DailyFact.objects.create(company=self.company,project_work=self.second_work,date=self.day,actuation_type='DAY',actuation_reason='x',actuation_reason_ids=[reason.pk])

    def test_only_head_admin_review_and_foreign_target_rejected(self):
        fact,reason=self.marked_fact()
        with self.assertRaises(PermissionDenied):decide(self.author,reason.pk,'approve',0)
        response=self.client.post(reverse('production:actuation_reason_review',args=[reason.pk]),{'action':'approve','revision':0})
        self.assertEqual(response.status_code,403)
        other=Company.objects.create(name='Foreign reasons')
        foreign=ActuationReason.objects.create(company=other,name='Foreign',normalized_name='foreign',status='APPROVED')
        with self.assertRaises(ValidationError):decide(self.head,reason.pk,'replace',0,foreign.pk)
        reason.refresh_from_db();self.assertEqual(reason.status,'PENDING')

    def test_pool_multiple_heads_and_admin_fallback(self):
        self.head.delete();reason=propose(self.author,'Первая');self.assertEqual(reason.task.assigned_to,self.users['ADMIN'])
        heads=[User.objects.create_user(username=f'head-{i}',company=self.company,role=Role.objects.get(code='PRODUCTION_HEAD')) for i in range(2)]
        from .actuation_reasons import sync_company
        sync_company(self.company);reason.refresh_from_db();reason.task.refresh_from_db()
        self.assertIsNone(reason.task.assigned_to_id)
        for head in heads:self.assertTrue(visible(head).filter(pk=reason.task_id).exists())

    def test_multi_file_bulk_upload_download_idempotence_and_storage_reuse(self):
        fact,reason=self.marked_fact();second,_=self.marked_fact(self.second_work)
        for f in [fact,second]:self.claim(f)
        batch=uuid4();response=self.upload([fact,second],batch=batch)
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(ActuationDocument.objects.count(),4)
        self.assertEqual(len(list((Path(self.temp.name)/'private').rglob('*'))),3) # one company directory, two physical files
        self.assertEqual(self.upload([fact,second],batch=batch).status_code,200)
        self.assertEqual(ActuationDocument.objects.count(),4)
        record=ActuationDocument.objects.first()
        download=self.client.get(reverse('production:actuation_document_download',args=[record.pk]))
        self.assertEqual(download.status_code,200);self.assertEqual(b''.join(download.streaming_content),b'%PDF-test')
        self.assertIn('attachment',download['Content-Disposition']);self.assertEqual(download['X-Content-Type-Options'],'nosniff')
        self.assertTrue(record.file.storage.exists(record.file.name))
        from .history_models import FactRevision
        self.assertEqual(FactRevision.objects.filter(entity_type='production.actuationdocument',operation='CREATE').count(),4)
        with self.assertRaises(ValueError):record.file.url

    def test_file_upload_keeps_nonzero_actuated_volume(self):
        fact,reason=self.marked_fact();fact.actual_quantity=12;fact.save();self.claim(fact)
        response=self.upload([fact])
        self.assertEqual(response.status_code,200,response.content)
        fact.refresh_from_db()
        self.assertEqual(fact.actual_quantity,12)
        self.assertEqual(ActuationDocument.objects.filter(fact=fact,removed_at=None).count(),2)

    def test_same_batch_with_different_bytes_is_rejected(self):
        fact,_=self.marked_fact();self.claim(fact);batch=uuid4()
        self.assertEqual(self.upload([fact],batch=batch).status_code,200)
        response=self.upload([fact],batch=batch,files=[SimpleUploadedFile('акт.pdf',b'%PDF-fake')])
        self.assertEqual(response.status_code,400);self.assertEqual(ActuationDocument.objects.count(),2)

    def test_files_require_scope_current_actuation_and_own_claim(self):
        fact,_=self.marked_fact()
        self.assertEqual(self.upload([fact]).status_code,400)
        self.claim(fact,self.second,uuid4());self.assertEqual(self.upload([fact]).status_code,400)
        self.claim(fact);fact.actuation_type='';fact.actuation_reason='';fact.actuation_reason_ids=[];fact.save()
        self.assertEqual(self.upload([fact]).status_code,400);self.assertFalse(ActuationDocument.objects.exists())

    def test_bad_file_rolls_back_batch_and_filesystem_failure_cleans_storage(self):
        fact,_=self.marked_fact();self.claim(fact)
        bad=[SimpleUploadedFile('ok.pdf',b'123'),SimpleUploadedFile('run.exe',b'456')]
        self.assertEqual(self.upload([fact],files=bad).status_code,400)
        self.assertFalse(ActuationDocument.objects.exists())
        with patch.object(ActuationDocument,'save',side_effect=ValidationError('Ошибка сохранения')):
            self.assertEqual(self.upload([fact]).status_code,400)
        self.assertFalse(ActuationDocument.objects.exists())
        self.assertFalse(any(p.is_file() for p in Path(self.temp.name).rglob('*')))

    def test_download_denies_other_company_and_unassigned_object(self):
        fact,_=self.marked_fact();self.claim(fact);self.assertEqual(self.upload([fact]).status_code,200)
        record=ActuationDocument.objects.first();url=reverse('production:actuation_document_download',args=[record.pk])
        outsider=User.objects.create_user(username='outsider',company=self.company,role=Role.objects.get(code='DISPATCHER'))
        self.client.force_login(outsider);self.assertEqual(self.client.get(url).status_code,404)
        other=Company.objects.create(name='Other company docs');outsider.company=other;outsider.save()
        self.assertEqual(self.client.get(url).status_code,404)

    def test_remove_is_soft_and_clear_actuation_keeps_other_files(self):
        fact,_=self.marked_fact();self.claim(fact);self.assertEqual(self.upload([fact]).status_code,200)
        record=ActuationDocument.objects.first()
        response=self.client.post(reverse('production:actuation_document_remove',args=[record.pk]),dict(self.scope,session=str(self.session)))
        self.assertEqual(response.status_code,200);record.refresh_from_db();self.assertIsNotNone(record.removed_at)
        self.assertTrue(record.file.storage.exists(record.file.name))
        self.assertEqual(self.client.get(reverse('production:actuation_document_download',args=[record.pk])).status_code,404)
        fact.actuation_type='';fact.actuation_reason='';fact.actuation_reason_ids=[];fact.save()
        self.assertEqual(ActuationDocument.objects.filter(removed_at=None).count(),1)
        fact.delete();self.assertEqual(ActuationDocument.objects.filter(fact=None).count(),2)

    def test_upload_limits_are_validated_before_writing(self):
        fact,_=self.marked_fact();self.claim(fact)
        for limit in ['MAX_FILE','MAX_TOTAL','MAX_COUNT']:
            with patch('apps.production.actuation_views.'+limit,1):
                self.assertEqual(self.upload([fact]).status_code,400)
        self.assertFalse(ActuationDocument.objects.exists())
        self.assertFalse(any(p.is_file() for p in Path(self.temp.name).rglob('*')))
