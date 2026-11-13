from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .dispatch_models import EmployeeActivity, ProductionBatchReceipt
from .models import DailyFact
from . import test_production_entry as fixtures


class ProductionBatchSaveTests(TestCase):
    setUpTestData=classmethod(fixtures.ProductionEntryTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.users['DISPATCHER'])
        self.batch_url=reverse('production:production_batch_save')

    def request(self,rows,batch_id=None):
        return self.client.post(self.batch_url,{
            'batch_id':str(batch_id or uuid4()),'construction_object':self.obj.pk,
            'date':self.day.isoformat(),'rows':rows,
        },content_type='application/json')

    def work_row(self,prefix='works-87'):
        return {'kind':'works','prefix':prefix,'id':None,'version':None,'values':{
            'project_work':str(self.work.pk),'work_item':'','complex_object_filter':'',
            'operational_name':'','operational_unit':'','actual_quantity':'7','comment':'',
            'actuation_type':'','actuation_reason':'','actuation_reasons_present':'1',
            'work_claim_token':'','DELETE':'',
        }}

    def person_row(self,prefix='labor-503'):
        return {'kind':'labor','prefix':prefix,'id':None,'version':None,'values':{
            'employee':str(self.employee.pk),'shift':'1','planning_position':'','work_mode':'pending',
            'work':'','work_item':'','general':'','equipment':'','activity':'',
            'person_claim_token':'','DELETE':'',
        }}

    def test_compact_batch_saves_only_supplied_rows_without_report(self):
        response=self.request([self.work_row(),self.person_row()])
        self.assertEqual(response.status_code,200,response.content)
        result=response.json()
        self.assertNotIn('report',result)
        self.assertTrue(result['report_dirty'])
        self.assertEqual({row['prefix'] for row in result['rows']},{'works-87','labor-503'})
        self.assertEqual(DailyFact.objects.get().actual_quantity,7)
        self.assertEqual(EmployeeActivity.objects.get().employee,self.employee)

    def test_legacy_empty_actuation_reasons_do_not_block_quantity_save(self):
        row=self.work_row();row['values']['actuation_reasons']=''
        response=self.request([row])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(DailyFact.objects.get().actual_quantity,7)

    def test_empty_batch_after_completed_autosave_is_a_safe_noop(self):
        response=self.request([])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json(),{'rows':[],'report_dirty':False,'noop':True})

    def test_lost_response_can_repeat_same_batch_without_duplicates(self):
        batch_id=uuid4();rows=[self.work_row()]
        first=self.request(rows,batch_id)
        second=self.request(rows,batch_id)
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(second.status_code,200,second.content)
        self.assertTrue(second.json()['replayed'])
        self.assertEqual(DailyFact.objects.count(),1)
        self.assertEqual(ProductionBatchReceipt.objects.filter(pk=batch_id).count(),1)

    def test_batch_is_limited_and_key_cannot_be_reused_for_other_data(self):
        oversized=self.request([self.work_row(str(index)) for index in range(16)])
        self.assertEqual(oversized.status_code,400,oversized.content)
        batch_id=uuid4();self.assertEqual(self.request([self.work_row()],batch_id).status_code,200)
        changed=self.work_row();changed['values']['actual_quantity']='8'
        self.assertEqual(self.request([changed],batch_id).status_code,409)
        self.assertEqual(DailyFact.objects.get().actual_quantity,7)

    def test_stale_row_version_does_not_overwrite_newer_value(self):
        created=self.request([self.work_row()]).json()['rows'][0]
        fact=DailyFact.objects.get();fact.actual_quantity=9;fact.save()
        row=self.work_row();row.update(id=fact.pk,version=created['version']);row['values']['actual_quantity']='2'
        response=self.request([row])
        self.assertEqual(response.status_code,409,response.content)
        fact.refresh_from_db();self.assertEqual(fact.actual_quantity,9)
