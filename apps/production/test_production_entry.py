from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User, Role
from apps.resources.models import BrigadeGroup, Brigade, Employee
from apps.resources.position_catalog import position_for
from apps.works.models import ProjectWork
from apps.planning.models import DailyScheduleEntry
from . import test_dispatch as fixtures
from .dispatch import fact_sections_confirmed
from .dispatch_models import EmployeeActivity, EmployeeWorkLink, ProductionEntryDay, WorkFactClaim, FactSectionDay
from .models import DailyFact, LaborFact


class ProductionEntryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.DispatcherTests.setUpTestData.__func__(cls)
        cls.second=User.objects.create_user(username='production-second',company=cls.company,role=Role.objects.get(code='DISPATCHER'))
        cls.second.assigned_objects.add(cls.obj);cls.users['SECOND']=cls.second
        cls.second_work=ProjectWork.objects.create(company=cls.company,section=cls.work.section,name='Work second',unit='m')
        DailyScheduleEntry.objects.create(schedule=cls.schedule,key=f'work:{cls.second_work.pk}',kind='work',label=cls.second_work.name,
            metadata={'id':cls.second_work.pk,'kind':'SIMPLE'},rows=[{'date':str(cls.day),'item_id':None,'quantity':'3'}])
        cls.general_group=BrigadeGroup.objects.create(company=cls.company,name='Постоянный персонал',is_general_staff=True)
        cls.cook_position=position_for(Brigade.objects.create(company=cls.company,name='Повар',group=cls.general_group))
        cls.cook=Employee.objects.create(company=cls.company,last_name='Поваров',first_name='Пётр',position=cls.cook_position)

    payload=fixtures.DispatcherTests.payload

    def row(self,data,work):
        return next(name[:-len('project_work')] for name,value in data.items() if name.startswith('works-') and name.endswith('-project_work') and str(value)==str(work.pk))

    def post(self,code,data):
        self.client.force_login(self.users[code]);response=self.client.post(self.url,data)
        details={'error':response.context.get('error'),'groups':{group['prefix']:group['formset'].errors for group in response.context['groups']}} if response.status_code==400 and response.context else {}
        return response,details

    def save_work(self,code,work,quantity,**extra):
        data=self.payload(code,'works');data[self.row(data,work)+'actual_quantity']=str(quantity);data.update(extra)
        response,details=self.post(code,data);self.assertEqual(response.status_code,302,details)
        return data

    def test_production_work_and_multiple_employee_targets_are_saved_without_double_count(self):
        data=self.payload('DISPATCHER','works')
        data[self.row(data,self.work)+'actual_quantity']='5';data[self.row(data,self.second_work)+'actual_quantity']='3'
        data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'works',
            'labor-0-work_targets':[f'work:{self.work.pk}',f'work:{self.second_work.pk}'],'action':'confirm'})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        activity=EmployeeActivity.objects.get(employee=self.employee)
        self.assertEqual(activity.reported_by,self.users['DISPATCHER'])
        self.assertEqual(set(activity.work_links.values_list('work_id',flat=True)),{self.work.pk,self.second_work.pk})
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
        self.assertTrue(ProductionEntryDay.objects.get(dispatcher=self.users['DISPATCHER']).confirmed_at)

    def test_worked_without_job_is_explicit_and_general_staff_is_not_offered_in_production(self):
        data=self.payload('DISPATCHER','works')
        data[self.row(data,self.work)+'actual_quantity']='5'
        data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'general'})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        activity=EmployeeActivity.objects.get(employee=self.employee)
        self.assertTrue(activity.general);self.assertIsNone(activity.work_id);self.assertFalse(activity.work_links.exists())
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
        data=self.payload('DISPATCHER','works');data['labor-1-employee']=self.cook.pk;data['labor-1-work_mode']='general'
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,400)
        self.assertFalse(EmployeeActivity.objects.filter(employee=self.cook).exists())

    def test_previous_jobs_and_people_prefill_only_for_this_dispatcher_without_copying_quantities(self):
        previous=self.day-timedelta(days=1)
        third=ProjectWork.objects.create(company=self.company,section=self.work.section,name='Вчерашняя работа',unit='шт')
        DailyFact.objects.create(company=self.company,date=previous,project_work=third,actual_quantity=9,reported_by=self.users['DISPATCHER'])
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=previous,employee=self.employee,position=self.employee.position,
            work=third,shift=2,reported_by=self.users['DISPATCHER'])
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=previous,employee=self.cook,position=self.cook_position,
            general=True,reported_by=self.users['PERSONNEL_DISPATCHER'])
        data=self.payload('DISPATCHER','works')
        self.assertEqual(data[self.row(data,third)+'actual_quantity'],'')
        self.assertEqual(str(data['labor-0-employee']),str(self.employee.pk))
        self.assertEqual(str(data['labor-0-shift']),'2')
        self.assertEqual(data['labor-0-work_targets'],[f'work:{third.pk}'])
        self.assertFalse(EmployeeActivity.objects.filter(date=self.day).exists())
        data[self.row(data,third)+'actual_quantity']='1';data[self.row(data,third)+'deviation_reason']=self.reason.pk
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        self.assertEqual(EmployeeActivity.objects.filter(employee=self.employee,date=self.day).count(),1)
        self.assertEqual(DailyFact.objects.get(project_work=third,date=previous).actual_quantity,9)
        other=self.payload('SECOND','works')
        self.assertFalse(any(str(value)==str(third.pk) for name,value in other.items() if name.endswith('-project_work')))

    def test_constant_personnel_reuses_previous_day_and_needs_no_job(self):
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day-timedelta(days=1),employee=self.cook,
            position=self.cook_position,general=True,reported_by=self.users['PERSONNEL_DISPATCHER'])
        data=self.payload('PERSONNEL_DISPATCHER','labor')
        self.assertEqual(str(data['labor-0-employee']),str(self.cook.pk))
        response,details=self.post('PERSONNEL_DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        self.assertTrue(EmployeeActivity.objects.get(employee=self.cook,date=self.day).general)

    def test_distinct_dispatchers_can_save_same_day_from_stale_day_versions(self):
        first=self.payload('DISPATCHER','works');second=self.payload('SECOND','works')
        first[self.row(first,self.work)+'actual_quantity']='5';second[self.row(second,self.second_work)+'actual_quantity']='3'
        self.assertEqual(self.post('DISPATCHER',first)[0].status_code,302)
        response,details=self.post('SECOND',second);self.assertEqual(response.status_code,302,details)
        self.assertEqual(DailyFact.objects.count(),2)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).reported_by,self.users['DISPATCHER'])
        self.assertEqual(DailyFact.objects.get(project_work=self.second_work).reported_by,self.second)
        first=self.payload('DISPATCHER','works');first['action']='confirm'
        response,details=self.post('DISPATCHER',first);self.assertEqual(response.status_code,302,details)
        self.assertTrue(fact_sections_confirmed(self.users['DISPATCHER'],self.company.pk,self.obj.pk,self.day))
        self.assertFalse(fact_sections_confirmed(self.second,self.company.pk,self.obj.pk,self.day))
        self.assertIsNone(FactSectionDay.objects.get(section='works').confirmed_at)
        second=self.payload('SECOND','works');second['action']='confirm'
        response,details=self.post('SECOND',second);self.assertEqual(response.status_code,302,details)
        self.assertIsNotNone(FactSectionDay.objects.get(section='works').confirmed_at)

    def test_same_row_conflict_and_untouched_stale_row_does_not_overwrite(self):
        self.save_work('DISPATCHER',self.work,5)
        self.save_work('DISPATCHER',self.second_work,3)
        left=self.payload('DISPATCHER','works');right=self.payload('DISPATCHER','works')
        left[self.row(left,self.work)+'actual_quantity']='4';left[self.row(left,self.work)+'deviation_reason']=self.reason.pk
        right[self.row(right,self.second_work)+'actual_quantity']='2';right[self.row(right,self.second_work)+'deviation_reason']=self.reason.pk
        response,details=self.post('DISPATCHER',left);self.assertEqual(response.status_code,302,details)
        response,details=self.post('DISPATCHER',right);self.assertEqual(response.status_code,302,details)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actual_quantity,4)
        stale=self.payload('DISPATCHER','works');fresh=self.payload('DISPATCHER','works')
        fresh[self.row(fresh,self.work)+'actual_quantity']='3';fresh[self.row(fresh,self.work)+'deviation_reason']=self.reason.pk
        self.assertEqual(self.post('DISPATCHER',fresh)[0].status_code,302)
        stale[self.row(stale,self.work)+'actual_quantity']='1';stale[self.row(stale,self.work)+'deviation_reason']=self.reason.pk
        response,details=self.post('DISPATCHER',stale);self.assertEqual(response.status_code,400)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actual_quantity,3)

    def test_group_flag_roundtrip_and_older_update_client_preserves_flag(self):
        self.client.force_login(self.users['ADMIN'])
        url=reverse('resources:brigade_group_update',args=[self.general_group.pk])
        self.assertEqual(self.client.post(url,{'name':self.general_group.name}).status_code,302)
        self.general_group.refresh_from_db();self.assertTrue(self.general_group.is_general_staff)
        self.assertEqual(self.client.post(url,{'name':self.general_group.name,'staffing_mode_present':'1'}).status_code,302)
        self.general_group.refresh_from_db();self.assertFalse(self.general_group.is_general_staff)

    def claim(self,code,session,key=None,action='take'):
        self.client.force_login(self.users[code])
        return self.client.post(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),
            'session':str(session),'key':key or f'work:{self.work.pk}','action':action},content_type='application/json')

    def test_unsaved_claim_visible_and_exclusive_different_work_allowed_and_expiry_releases(self):
        one,two=uuid4(),uuid4()
        self.assertEqual(self.claim('DISPATCHER',one).status_code,200)
        self.assertFalse(DailyFact.objects.exists())
        self.client.force_login(self.second)
        state=self.client.get(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),'session':str(two)}).json()['claims'][0]
        self.assertEqual(state['owner'],self.users['DISPATCHER'].username)
        self.assertFalse(state['mine'])
        self.assertEqual(self.claim('SECOND',two).status_code,409)
        self.assertEqual(self.claim('SECOND',two,f'work:{self.second_work.pk}').status_code,200)
        self.claim('SECOND',two,action='release')
        self.assertEqual(WorkFactClaim.objects.filter(work=self.work).count(),1,'Another dispatcher cannot release the reservation')
        WorkFactClaim.objects.filter(work=self.work).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.claim('SECOND',two).status_code,200)
        self.assertEqual(self.claim('DISPATCHER',one,action='renew').status_code,409)

    def test_other_dispatcher_cannot_bypass_claim_by_direct_post_and_save_releases_it(self):
        one,two=uuid4(),uuid4()
        first=self.payload('DISPATCHER','works');second=self.payload('SECOND','works')
        self.assertEqual(self.claim('SECOND',two).status_code,200)
        first[self.row(first,self.work)+'actual_quantity']='5'
        self.assertEqual(self.post('DISPATCHER',first)[0].status_code,400)
        self.assertFalse(DailyFact.objects.exists())
        prefix=self.row(second,self.work);second[prefix+'actual_quantity']='5';second[prefix+'work_claim_token']=str(two);second['claim_session']=str(two)
        response,details=self.post('SECOND',second);self.assertEqual(response.status_code,302,details)
        self.assertFalse(WorkFactClaim.objects.filter(work=self.work).exists())
        self.assertEqual(DailyFact.objects.get().reported_by,self.second)
        self.assertEqual(self.claim('DISPATCHER',one).status_code,409)

    def test_presence_draft_can_be_saved_before_links_and_confirmation_requires_review(self):
        data=self.payload('DISPATCHER','works')
        data['labor-0-employee']=self.employee.pk;data['labor-0-work_mode']='pending'
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        row=EmployeeActivity.objects.get(employee=self.employee)
        self.assertFalse(row.assignment_reviewed);self.assertFalse(row.general);self.assertIsNone(row.work_id)
        data=self.payload('DISPATCHER','works');data['action']='confirm'
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,400)
        self.assertIn('Связи',details['error'])
        data=self.payload('DISPATCHER','works');data.update({'labor-0-work_mode':'general','action':'confirm'})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        row.refresh_from_db();self.assertTrue(row.assignment_reviewed);self.assertTrue(row.general)

    def test_employee_claim_protects_unsaved_presence_and_links_until_release(self):
        one,two=uuid4(),uuid4();key=f'employee:{self.employee.pk}:1'
        self.assertEqual(self.claim('DISPATCHER',one,key).status_code,200)
        self.assertFalse(EmployeeActivity.objects.exists())
        self.assertEqual(self.claim('SECOND',two,key).status_code,409)
        data=self.payload('SECOND','works');data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'general'})
        response,details=self.post('SECOND',data);self.assertEqual(response.status_code,400)
        self.assertFalse(EmployeeActivity.objects.exists())
        data=self.payload('DISPATCHER','works');data.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'general','labor-0-person_claim_token':str(one),'claim_session':str(one)})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        from .dispatch_models import EmployeeFactClaim
        self.assertFalse(EmployeeFactClaim.objects.exists())
        self.assertEqual(self.claim('SECOND',two,key).status_code,409)

    def test_employee_reservation_is_global_across_objects_but_per_shift(self):
        from apps.projects.models import ConstructionObject
        other=ConstructionObject.objects.create(company=self.company,project=self.project,name='Другой объект')
        self.second.assigned_objects.add(other)
        one,two=uuid4(),uuid4();key=f'employee:{self.employee.pk}:1'
        self.assertEqual(self.claim('DISPATCHER',one,key).status_code,200)
        self.client.force_login(self.second)
        response=self.client.post(reverse('production:work_claims'),{'construction_object':other.pk,'date':str(self.day),'session':str(two),'key':key,'action':'take'},content_type='application/json')
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.claim('SECOND',two,f'employee:{self.employee.pk}:2').status_code,200)

    def test_distinct_people_can_save_from_same_baseline_and_stale_links_cannot_overwrite(self):
        another=Employee.objects.create(company=self.company,position=self.employee.position,last_name='Другой',first_name='Сотрудник')
        left=self.payload('DISPATCHER','works');right=self.payload('SECOND','works')
        left.update({'labor-0-employee':self.employee.pk,'labor-0-work_mode':'general'})
        right.update({'labor-0-employee':another.pk,'labor-0-work_mode':'general'})
        self.assertEqual(self.post('DISPATCHER',left)[0].status_code,302)
        response,details=self.post('SECOND',right);self.assertEqual(response.status_code,302,details)
        self.assertEqual(EmployeeActivity.objects.count(),2)
        stale=self.payload('DISPATCHER','works');fresh=self.payload('DISPATCHER','works')
        fresh['labor-0-activity']='Новое описание'
        self.assertEqual(self.post('DISPATCHER',fresh)[0].status_code,302)
        stale['labor-0-activity']='Старое описание'
        response,details=self.post('DISPATCHER',stale);self.assertEqual(response.status_code,400)
        self.assertEqual(EmployeeActivity.objects.get(employee=self.employee).activity,'Новое описание')

    def test_other_dispatcher_save_does_not_prevent_my_previous_roster_prefill(self):
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day-timedelta(days=1),employee=self.employee,
            position=self.employee.position,general=True,reported_by=self.second)
        self.save_work('DISPATCHER',self.work,5)
        data=self.payload('SECOND','works')
        self.assertEqual(str(data['labor-0-employee']),str(self.employee.pk))
        self.assertEqual(data['labor-0-work_mode'],'general')
        self.assertFalse(EmployeeActivity.objects.filter(date=self.day).exists())

    def test_claim_rejects_wrong_object_company_and_browser_session_cannot_release_another(self):
        one,two=uuid4(),uuid4()
        self.assertEqual(self.claim('DISPATCHER',one).status_code,200)
        self.assertEqual(self.claim('DISPATCHER',two,action='release').json()['released'],False)
        self.assertEqual(WorkFactClaim.objects.count(),1)
        self.client.force_login(self.second)
        response=self.client.post(reverse('production:work_claims'),{'construction_object':self.other.pk,'date':str(self.day),
            'session':str(two),'key':f'work:{self.work.pk}','action':'take'},content_type='application/json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.claim('DISPATCHER',one,action='release').json()['released'],True)

    def test_work_only_draft_retains_carried_people_until_roster_is_taken(self):
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day-timedelta(days=1),employee=self.employee,
            position=self.employee.position,general=True,reported_by=self.users['DISPATCHER'])
        data=self.payload('DISPATCHER','works');data[self.row(data,self.work)+'actual_quantity']='5'
        data['labor-0-person_editing']='0'
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        self.assertFalse(ProductionEntryDay.objects.get(dispatcher=self.users['DISPATCHER']).roster_started)
        data=self.payload('DISPATCHER','works')
        self.assertEqual(str(data['labor-0-employee']),str(self.employee.pk))
        data.update({'labor-0-person_editing':'0','action':'confirm'})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,400)
        data['labor-0-DELETE']='on'
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        data=self.payload('DISPATCHER','works');self.assertEqual(data['labor-0-employee'],'')
