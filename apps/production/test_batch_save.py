from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from .dispatch_models import EmployeeActivity, ProductionBatchReceipt
from .models import DailyFact
from . import test_production_entry as fixtures
from apps.works.models import ComplexObject, ProjectWork
from apps.projects.models import ConstructionObject


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

    def operational_row(self,work,prefix='works-901'):
        row=self.work_row(prefix);row['values'].update(project_work=str(work.pk),operational_name='  Уборка   мусора ',operational_unit=' РЕЙС ')
        return row

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

    def test_blank_work_quantity_is_saved_as_zero(self):
        row=self.work_row();row['values']['actual_quantity']=''
        response=self.request([row])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(DailyFact.objects.get().actual_quantity,0)

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

    def test_new_row_for_existing_work_opens_fact_instead_of_creating_duplicate(self):
        first=self.request([self.work_row()]).json()['rows'][0]
        changed=self.work_row();changed['values']['actual_quantity']='11'
        response=self.request([changed])
        self.assertEqual(response.status_code,409,response.content)
        issue=response.json()['errors'][0]
        self.assertEqual((issue['code'],issue['existing_id']),('existing_work',first['id']))
        self.assertEqual(DailyFact.objects.count(),1)
        self.assertEqual(DailyFact.objects.get().actual_quantity,7)

    def test_batch_is_limited_and_key_cannot_be_reused_for_other_data(self):
        oversized=self.request([self.work_row(str(index)) for index in range(16)])
        self.assertEqual(oversized.status_code,400,oversized.content)
        batch_id=uuid4();self.assertEqual(self.request([self.work_row()],batch_id).status_code,200)
        changed=self.work_row();changed['values']['actual_quantity']='8'
        self.assertEqual(self.request([changed],batch_id).status_code,409)
        self.assertEqual(DailyFact.objects.get().actual_quantity,7)

    def test_operational_duplicate_requires_explicit_confirmation(self):
        complex_object=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.work.section,name='Прочее',allow_dispatcher_work_items=True)
        package=ProjectWork.objects.create(company=self.company,section=self.work.section,complex_object=complex_object,name='Прочее',kind='COMPOSITE',unit='компл')
        first=self.request([self.operational_row(package)])
        self.assertEqual(first.status_code,200,first.content)
        duplicate=self.operational_row(package);duplicate['values'].update(operational_name='уборка мусора',operational_unit='рейс')
        blocked=self.request([duplicate])
        self.assertEqual(blocked.status_code,409,blocked.content)
        issue=blocked.json()['errors'][0]
        self.assertEqual(issue['code'],'operational_duplicate')
        duplicate['allow_duplicate_operational']=True
        confirmed=self.request([duplicate])
        self.assertEqual(confirmed.status_code,200,confirmed.content)
        self.assertEqual(DailyFact.objects.filter(operational_key__isnull=False).count(),2)

    def test_existing_person_returns_row_to_open_instead_of_model_error(self):
        first=self.request([self.person_row()])
        self.assertEqual(first.status_code,200,first.content)
        saved=first.json()['rows'][0]
        duplicate=self.person_row('labor-504')
        blocked=self.request([duplicate])
        self.assertEqual(blocked.status_code,409,blocked.content)
        issue=blocked.json()['errors'][0]
        self.assertEqual((issue['code'],issue['existing_id']),('existing_person',saved['id']))
        self.assertNotIn('Employee activity',blocked.json()['error'])

    def test_employee_already_recorded_for_day_can_be_added_to_other_shift_after_archiving(self):
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,
            position=self.employee.position,shift=1,entry_direction='works',reported_by=self.users['DISPATCHER'])
        self.employee.is_active=False;self.employee.save(update_fields=['is_active'])
        row=self.person_row();row['values']['shift']='2'
        response=self.request([row])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(set(EmployeeActivity.objects.filter(employee=self.employee).values_list('shift',flat=True)),{1,2})

    def test_person_on_other_object_returns_exact_location(self):
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой объект')
        activity=EmployeeActivity.objects.create(company=self.company,construction_object=other,date=self.day,employee=self.employee,position=self.employee.position,shift=1)
        blocked=self.request([self.person_row()])
        self.assertEqual(blocked.status_code,409,blocked.content)
        issue=blocked.json()['errors'][0]
        self.assertEqual((issue['code'],issue['construction_object'],issue['existing_id']),('person_other_object',other.pk,activity.pk))
        self.assertEqual((issue['date'],issue['shift']),(self.day.isoformat(),1))

    def test_stale_row_version_does_not_overwrite_newer_value(self):
        created=self.request([self.work_row()]).json()['rows'][0]
        fact=DailyFact.objects.get();fact.actual_quantity=9;fact.save()
        row=self.work_row();row.update(id=fact.pk,version=created['version']);row['values']['actual_quantity']='2'
        response=self.request([row])
        self.assertEqual(response.status_code,409,response.content)
        conflict=response.json()['errors'][0]
        self.assertEqual(conflict['prefix'],row['prefix'])
        self.assertEqual(conflict['version'],fact.updated_at.isoformat())
        fact.refresh_from_db();self.assertEqual(fact.actual_quantity,9)
        row['version']=conflict['version']
        retry=self.request([row])
        self.assertEqual(retry.status_code,200,retry.content)
        fact.refresh_from_db();self.assertEqual(fact.actual_quantity,2)
