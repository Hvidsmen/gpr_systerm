from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from apps.resources.models import Employee
from apps.personnel import housing,lodging_sync
from apps.personnel.models import CampStay,Person,Camp,LodgingResolution
from apps.production.dispatch_models import EmployeeActivity
from . import test_production_entry as fixtures
from .employee_pool import prepare,activity_direction


class ProductionEmployeePoolTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures.ProductionEntryTests.setUpTestData.__func__(cls)
    payload=fixtures.ProductionEntryTests.payload
    post=fixtures.ProductionEntryTests.post
    def setUp(self):
        self.operator=self.users['DISPATCHER'];self.client.force_login(self.operator)
        from apps.personnel.models import CampObjectLink
        self.home=Camp.objects.filter(company=self.company,object_links__construction_object=self.obj).first()
        self.other=Camp.objects.create(company=self.company,name='Другой ВЖГ')
        self.away=Employee.objects.create(company=self.company,last_name='Андреев',first_name='Другой',employee_number='AWAY',position=self.employee.position)
        self.unknown=Employee.objects.create(company=self.company,last_name='Алексеев',first_name='Не размещён',employee_number='UNKNOWN',position=self.employee.position)
        self.missing_role=Employee.objects.create(company=self.company,last_name='Без',first_name='Должности',employee_number='NO-ROLE')
        self.inactive=Employee.objects.create(company=self.company,last_name='Архив',first_name='Сотрудник',is_active=False,position=self.employee.position)
        person=housing.person_for(self.away);CampStay.objects.create(company=self.company,camp=self.other,person=person,arrived_on=self.day-timedelta(days=5))
    def save_employee(self,employee,**extra):
        data=self.payload('DISPATCHER','works');data.update({'labor-0-employee':employee.pk,'labor-0-work_mode':'general',**extra})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        return EmployeeActivity.objects.get(employee=employee,date=self.day)
    def test_group_order_is_local_then_other_camps_then_unknown_with_number_and_dated_location(self):
        pool=prepare(self.obj,self.day);by_id=pool['locations']
        self.assertEqual(by_id[str(self.employee.pk)]['group'],0)
        self.assertEqual(by_id[str(self.away.pk)]['group'],1);self.assertEqual(by_id[str(self.unknown.pk)]['group'],2)
        self.assertNotIn(str(self.inactive.pk),by_id)
        choices=[pk for _,values in pool['groups'] for pk,_ in values]
        self.assertLess(choices.index(self.employee.pk),choices.index(self.away.pk));self.assertLess(choices.index(self.away.pk),choices.index(self.unknown.pk))
        self.assertIn('AWAY',str(pool['groups']));self.assertIn(self.other.name,str(pool['groups']))
        stay=CampStay.objects.get(person__employee=self.away);stay.departed_on=self.day;stay.save()
        self.assertEqual(prepare(self.obj,self.day)['locations'][str(self.away.pk)]['group'],2)
        self.assertTrue(prepare(self.obj,self.day)['locations'][str(self.away.pk)]['rest'])
    def test_save_unknown_active_worker_preserves_fact_and_creates_only_a_live_reconciliation_item(self):
        row=self.save_employee(self.unknown,action='confirm')
        self.assertEqual(row.entry_direction,'works')
        self.assertFalse(CampStay.objects.filter(person__employee=self.unknown).exists())
        issue=next(r for r in lodging_sync.issues(self.company,[self.home],self.day) if r['employee'].pk==self.unknown.pk)
        self.assertEqual(issue['code'],'PRODUCTION');self.assertEqual(issue['production'][0].pk,row.pk)
    def test_employee_without_staff_position_and_constant_staff_are_still_allowed(self):
        row=self.save_employee(self.missing_role);self.assertIsNone(row.position_id)
        # A later form may also enter staff from a general-personnel group.
        data=self.payload('DISPATCHER','works');data.update({'labor-1-employee':self.cook.pk,'labor-1-work_mode':'general'})
        response,details=self.post('DISPATCHER',data);self.assertEqual(response.status_code,302,details)
        cook=EmployeeActivity.objects.get(employee=self.cook);self.assertEqual(activity_direction(cook),'works')
        from .work_claims import can_edit_person
        self.assertTrue(can_edit_person(self.second,cook));self.assertFalse(can_edit_person(self.users['PERSONNEL_DISPATCHER'],cook))
    def test_other_camp_selection_and_claim_are_allowed_but_not_same_employee_same_shift_twice(self):
        session=str(uuid4());url=reverse('production:work_claims')
        payload={'construction_object':self.obj.pk,'date':str(self.day),'session':session,'key':f'employee:{self.away.pk}:1','action':'take'}
        self.assertEqual(self.client.post(url,payload,content_type='application/json').status_code,200)
        from django.test import Client
        other=Client();other.force_login(self.second);payload['session']=str(uuid4())
        self.assertEqual(other.post(url,payload,content_type='application/json').status_code,409)
        payload['session']=session;payload['action']='release';self.client.post(url,payload,content_type='application/json')
        self.save_employee(self.away)
        data=self.payload('DISPATCHER','works');data.update({'labor-1-employee':self.away.pk,'labor-1-work_mode':'general'})
        response,_=self.post('DISPATCHER',data);self.assertEqual(response.status_code,400)
    def test_inactive_and_foreign_employee_are_rejected(self):
        foreign=Employee.objects.create(company=type(self.company).objects.create(name='Other company'),last_name='Foreign',first_name='Employee')
        for employee in [self.inactive,foreign]:
            data=self.payload('DISPATCHER','works');data.update({'labor-0-employee':employee.pk,'labor-0-work_mode':'general'})
            response,_=self.post('DISPATCHER',data);self.assertEqual(response.status_code,400)
    def test_reconciliation_can_be_explained_or_explicitly_placed_without_changing_production(self):
        row=self.save_employee(self.unknown);hr=self.users['PERSONNEL_DISPATCHER']
        issue=next(r for r in lodging_sync.issues(self.company,[self.home],self.day) if r['employee'].pk==self.unknown.pk)
        signed=lodging_sync.signed_issue(hr,issue)
        lodging_sync.explain(hr,self.home,self.day,signed,'Проживает вне ВЖГ')
        self.assertFalse(any(r['employee'].pk==self.unknown.pk for r in lodging_sync.issues(self.company,[self.home],self.day)))
        row.activity='Уточнён факт';row.save()
        with self.assertRaises(ValidationError):lodging_sync.confirm_production_arrival(hr,self.home,self.day,signed,self.other,self.day)
        issue=next(r for r in lodging_sync.issues(self.company,[self.home],self.day) if r['employee'].pk==self.unknown.pk)
        lodging_sync.confirm_production_arrival(hr,self.home,self.day,lodging_sync.signed_issue(hr,issue),self.other,self.day)
        self.assertTrue(CampStay.objects.filter(person__employee=self.unknown,camp=self.other).exists())
        row.refresh_from_db();self.assertEqual(row.activity,'Уточнён факт');self.assertEqual(row.construction_object,self.obj)
    def test_pending_roster_is_not_reported_as_worked_and_previous_worker_carries_without_camp(self):
        yesterday=self.day-timedelta(days=1)
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=yesterday,employee=self.unknown,position=self.unknown.position,entry_direction='works',general=True,assignment_reviewed=True,reported_by=self.operator)
        from .production_entry import previous_people
        date,rows=previous_people(self.obj,self.day,production=True,user=self.operator)
        self.assertEqual(date,yesterday);self.assertIn(self.unknown.pk,[r['employee'] for r in rows])
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.unknown,position=self.unknown.position,entry_direction='works',assignment_reviewed=False,reported_by=self.operator)
        self.assertFalse(any(r['employee'].pk==self.unknown.pk for r in lodging_sync.issues(self.company,[self.home],self.day)))

    def test_direction_backfill_preserves_existing_people_fields_and_source_ownership(self):
        from importlib import import_module
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        row=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.cook,position=self.cook.position,reported_by=self.operator,general=True)
        stamp=row.updated_at
        hr_row=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.unknown,position=self.unknown.position,reported_by=self.users['PERSONNEL_DISPATCHER'],general=True)
        legacy_general=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.cook,position=self.cook.position,shift=2,general=True)
        apps=MigrationExecutor(connection).loader.project_state([('production','0026_employee_activity_direction')]).apps
        import_module('apps.production.migrations.0026_employee_activity_direction').backfill(apps,connection.schema_editor())
        row.refresh_from_db();self.assertEqual(row.entry_direction,'works');self.assertEqual(row.updated_at,stamp);self.assertEqual(row.reported_by,self.operator)

        hr_row.refresh_from_db();legacy_general.refresh_from_db()
        self.assertEqual(hr_row.entry_direction,'labor');self.assertEqual(legacy_general.entry_direction,'labor')
