from uuid import uuid4
from django.urls import reverse
from django.test import TestCase
from .test_production_entry import ProductionEntryTests
from .dispatch_models import EmployeeActivity, EmployeeFactClaim
from .models import DailyFact


class EmployeeDuplicateTests(TestCase):
    setUpTestData=classmethod(ProductionEntryTests.setUpTestData.__func__)
    payload=ProductionEntryTests.payload
    post=ProductionEntryTests.post

    def two_rows(self, **extra):
        data=self.payload('DISPATCHER','works')
        data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'pending',**extra})
        for key,value in list(data.items()):
            if key.startswith('labor-0-'):data[key.replace('labor-0-','labor-1-',1)]=value
        data['labor-TOTAL_FORMS']='2'
        data['labor-1-id']=''
        return data

    def test_same_employee_and_shift_rejected_even_with_same_claim(self):
        data=self.two_rows()
        session=str(uuid4())
        self.client.force_login(self.users['DISPATCHER'])
        claim=self.client.post(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),'session':session,'key':f'employee:{self.employee.pk}:1','action':'take'},content_type='application/json')
        self.assertEqual(claim.status_code,200)
        data.update({'claim_session':session,'labor-0-person_claim_token':session,'labor-1-person_claim_token':session})
        work=next(key for key,value in data.items() if key.startswith('works-') and key.endswith('-project_work') and str(value)==str(self.work.pk))
        data[work.replace('project_work','actual_quantity')]='5'
        response,details=self.post('DISPATCHER',data)
        self.assertEqual(response.status_code,400,details)
        self.assertContains(response,'уже указан',status_code=400)
        self.assertFalse(EmployeeActivity.objects.exists())
        self.assertFalse(DailyFact.objects.exists(),'The invalid roster must not partially save works')
        self.assertEqual(EmployeeFactClaim.objects.count(),1,'The valid reservation remains available while correcting input')

    def test_same_employee_in_distinct_shifts_is_allowed(self):
        data=self.two_rows();data['labor-1-shift']='2'
        response,details=self.post('DISPATCHER',data)
        self.assertEqual(response.status_code,302,details)
        self.assertEqual(set(EmployeeActivity.objects.filter(employee=self.employee).values_list('shift',flat=True)),{1,2})

    def test_deleted_extra_duplicate_is_ignored(self):
        data=self.two_rows();data['labor-1-DELETE']='on'
        response,details=self.post('DISPATCHER',data)
        self.assertEqual(response.status_code,302,details)
        self.assertEqual(EmployeeActivity.objects.filter(employee=self.employee).count(),1)

    def test_saved_unedited_row_still_blocks_duplicate_and_message_is_readable(self):
        data=self.payload('DISPATCHER','works');data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'pending'})
        self.assertEqual(self.post('DISPATCHER',data)[0].status_code,302)
        data=self.payload('DISPATCHER','works')
        data.update({'labor-0-person_editing':'0','labor-1-employee':self.employee.pk,'labor-1-shift':'1','labor-1-work_mode':'pending','labor-1-person_editing':'1'})
        response,details=self.post('DISPATCHER',data)
        self.assertEqual(response.status_code,400,details)
        self.assertNotContains(response,'Employee activity',status_code=400)
        self.assertContains(response,'существующ',status_code=400)
        self.assertEqual(EmployeeActivity.objects.filter(employee=self.employee).count(),1)
