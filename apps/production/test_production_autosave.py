from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from .test_production_entry import ProductionEntryTests
from .dispatch_models import EmployeeActivity, EmployeeFactClaim
from .models import DailyFact
from .history_models import FactRevision


class ProductionAutosaveTests(TestCase):
    setUpTestData=classmethod(ProductionEntryTests.setUpTestData.__func__)
    payload=ProductionEntryTests.payload

    def setup_person(self):
        data=self.payload('DISPATCHER','works');session=str(uuid4())
        self.client.post(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),'session':session,'key':f'employee:{self.employee.pk}:1','action':'take'},content_type='application/json')
        data.update({'autosave':'1','claim_session':session,'labor-0-employee':self.employee.pk,'labor-0-work_mode':'pending','labor-0-person_claim_token':session})
        return data

    def apply_state(self,data,result):
        for key in ['production_snapshot','revision','labor_revision']:data[key]=str(result[key])
        for row in result['rows']:
            if row['id']:data[row['prefix']+'-id']=str(row['id'])
        data['labor-INITIAL_FORMS']='1'

    def test_save_edit_and_button_delete_rebase_without_reloading_or_releasing_edit(self):
        data=self.setup_person()
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200,response.content)
        result=response.json();record=EmployeeActivity.objects.get(employee=self.employee)
        self.assertEqual(result['rows'][0]['id'],record.pk)
        self.assertTrue(EmployeeFactClaim.objects.filter(employee=self.employee).exists())
        self.apply_state(data,result);data['labor-0-activity']='Изменено без перезагрузки'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200,response.content)
        record.refresh_from_db();self.assertEqual(record.activity,'Изменено без перезагрузки')
        self.apply_state(data,response.json());data['labor-0-DELETE']='on'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,200,response.content)
        self.assertTrue(response.json()['rows'][0]['deleted'])
        self.assertFalse(EmployeeActivity.objects.filter(pk=record.pk).exists())
        self.assertTrue(FactRevision.objects.filter(entity_type='production.employeeactivity',entity_id=str(record.pk),operation='DELETE',actor=self.users['DISPATCHER']).exists())

    def test_invalid_autosave_returns_errors_and_preserves_saved_values(self):
        data=self.setup_person();response=self.client.post(self.url,data);self.apply_state(data,response.json())
        data['labor-0-shift']='25'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,400)
        self.assertTrue(response.json()['errors'])
        self.assertEqual(EmployeeActivity.objects.get(employee=self.employee).shift,1)

    def test_duplicate_autosave_is_atomic_and_has_readable_error(self):
        data=self.setup_person()
        for key,value in list(data.items()):
            if key.startswith('labor-0-'):data[key.replace('labor-0-','labor-1-')]=value
        data['labor-TOTAL_FORMS']='2'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,400)
        self.assertIn('уже',str(response.json()))
        self.assertFalse(EmployeeActivity.objects.exists())
        self.assertFalse(DailyFact.objects.exists())

    def test_stale_autosave_does_not_overwrite_colleague_correction(self):
        data=self.setup_person();response=self.client.post(self.url,data);self.apply_state(data,response.json())
        self.client.post(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),'session':data['claim_session'],'action':'release_session'},content_type='application/json')
        other=self.payload('SECOND','works');other['labor-0-activity']='Исправление коллеги'
        self.assertEqual(self.client.post(self.url,other).status_code,302)
        self.client.force_login(self.users['DISPATCHER']);data['labor-0-activity']='Устаревшая форма'
        response=self.client.post(self.url,data)
        self.assertEqual(response.status_code,400)
        self.assertIn('друг',response.json()['error'])
        record=EmployeeActivity.objects.get(employee=self.employee)
        self.assertEqual(record.activity,'Исправление коллеги')
        self.assertEqual(record.reported_by,self.users['DISPATCHER'])

    def test_blank_work_quantity_does_not_block_isolated_person_deletion(self):
        data=self.setup_person();session=data['claim_session']
        prefix=next(name[:-len('project_work')] for name,value in data.items() if name.startswith('works-') and name.endswith('-project_work') and str(value)==str(self.work.pk))
        self.client.post(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),'session':session,'key':f'work:{self.work.pk}','action':'take'},content_type='application/json')
        data.update({prefix+'actual_quantity':'5',prefix+'work_editing':'1',prefix+'work_claim_token':session})
        response=self.client.post(self.url,data);self.assertEqual(response.status_code,200,response.content)
        self.apply_state(data,response.json());data['works-INITIAL_FORMS']='1'
        person=EmployeeActivity.objects.get(employee=self.employee);fact=DailyFact.objects.get(project_work=self.work)
        data[prefix+'actual_quantity']=''
        response=self.client.post(self.url,data);self.assertEqual(response.status_code,400)
        self.assertTrue(EmployeeActivity.objects.filter(pk=person.pk).exists())
        data.update({'labor-0-DELETE':'on',prefix+'work_editing':'0'})
        response=self.client.post(self.url,data);self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(EmployeeActivity.objects.filter(pk=person.pk).exists())
        fact.refresh_from_db();self.assertEqual(fact.actual_quantity,5)
        self.assertTrue(FactRevision.objects.filter(entity_type='production.employeeactivity',entity_id=str(person.pk),operation='DELETE').exists())
