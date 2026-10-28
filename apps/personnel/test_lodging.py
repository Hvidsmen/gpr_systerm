from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase,Client
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User,Role
from apps.resources.models import Employee
from apps.production.history_models import FactRevision
from . import test_housing as housing_fixtures
from .models import LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl,LodgingResolution,CampStay
from . import lodging,lodging_sync,housing


class LodgingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        housing_fixtures.HousingTests.setUpTestData.__func__(cls)
        role=Role.objects.get(code='COMMANDANT')
        cls.commandant=User.objects.create_user(username='commandant',company=cls.company,role=role)
        cls.colleague=User.objects.create_user(username='commandant-two',company=cls.company,role=role)
        cls.admin=User.objects.create_user(username='lodging-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
        cls.building=LodgingBuilding.objects.create(company=cls.company,camp=cls.a,name='Вагон 1',inventory_number='W-001',kind='Жилой')
        cls.bed=LodgingBed.objects.create(company=cls.company,building=cls.building,number='1')
        cls.bed2=LodgingBed.objects.create(company=cls.company,building=cls.building,number='2')
        cls.bed3=LodgingBed.objects.create(company=cls.company,building=cls.building,room='2',number='1')
        cls.foreign_building=LodgingBuilding.objects.create(company=cls.other,camp=cls.foreign_camp,name='Secret')
        cls.foreign_bed=LodgingBed.objects.create(company=cls.other,building=cls.foreign_building,number='1')
    def setUp(self):self.client.force_login(self.commandant)
    def edit(self,kind='CHECKIN',*,user=None,employee=None,bed=None,day=None,**kwargs):
        user=user or self.commandant;employee=employee or self.second_employee;day=day or self.day
        bed=(bed or self.bed) if kind in {'CHECKIN','MOVE'} else None
        session=str(uuid4());signed=lodging.claim(user,self.a,day,employee,bed,session)
        try:return lodging.move(user,kind,self.a,day,employee,bed=bed,session=session,signed=signed,**kwargs)
        except Exception:
            lodging.claim(user,self.a,day,employee,bed,session,'release')
            raise
    def issue(self,employee):return next(r for r in lodging_sync.issues(self.company,[self.a],self.day) if r['employee'].pk==employee.pk)

    def test_independent_checkin_and_hr_confirmation_do_not_reenter_employee(self):
        stay=self.edit()
        self.assertFalse(housing.available_employee_ids(self.obj,self.day).filter(person__employee=self.second_employee).exists())
        row=self.issue(self.second_employee);self.assertEqual(row['code'],'ARRIVAL')
        lodging_sync.confirm_arrival(self.hr,self.a,self.day,lodging_sync.signed_issue(self.hr,row),self.day)
        self.assertTrue(housing.available_employee_ids(self.obj,self.day).filter(person__employee=self.second_employee).exists())
        self.assertFalse(any(r['employee'].pk==self.second_employee.pk for r in lodging_sync.issues(self.company,[self.a],self.day)))
        stay.refresh_from_db();self.assertEqual(stay.recorded_by,self.commandant)
        self.assertEqual(CampStay.objects.get(person=self.second_person).recorded_by,self.hr)

    def test_hr_first_waiting_then_placed_and_internal_move_keeps_continuous_arrival(self):
        row=self.issue(self.employee);self.assertEqual(row['code'],'WAITING')
        self.edit(user=self.admin,employee=self.employee,day=self.stay.arrived_on)
        self.edit('MOVE',employee=self.employee,bed=self.bed2)
        self.assertFalse(any(r['employee'].pk==self.employee.pk for r in lodging_sync.issues(self.company,[self.a],self.day)))
        self.assertEqual(lodging.present(self.company,self.day).get(employee=self.employee).bed,self.bed2)
        self.assertEqual(lodging.present(self.company,self.day-timedelta(days=1)).get(employee=self.employee).bed,self.bed)

    def test_departures_independent_and_discrepancies_resolve(self):
        self.edit(user=self.admin,employee=self.employee,day=self.stay.arrived_on)
        housing.move(self.hr,'DEPARTURE',self.a,self.day,[self.person])
        self.assertEqual(self.issue(self.employee)['code'],'DEPARTED_OPEN')
        self.edit('CHECKOUT',employee=self.employee)
        self.assertFalse(any(r['employee'].pk==self.employee.pk for r in lodging_sync.issues(self.company,[self.a],self.day)))
        self.edit();housing.move(self.hr,'ARRIVAL',self.a,self.day,[self.second_person])
        self.edit('CHECKOUT');self.assertEqual(self.issue(self.second_employee)['code'],'AFTER_CHECKOUT')
        self.assertTrue(housing.present_stays(self.company,self.day).filter(person=self.second_person).exists())

    def test_unsaved_employee_and_bed_leases_block_colleague_and_other_tab(self):
        key=str(uuid4());signed=lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,key)
        with self.assertRaises(ValidationError):lodging.claim(self.colleague,self.a,self.day,self.employee,self.bed2,str(uuid4()))
        with self.assertRaises(ValidationError):lodging.claim(self.colleague,self.a,self.day,self.second_employee,self.bed,str(uuid4()))
        with self.assertRaises(ValidationError):lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,str(uuid4()))
        self.edit(user=self.colleague,bed=self.bed2)
        lodging.move(self.commandant,'CHECKIN',self.a,self.day,self.employee,bed=self.bed,session=key,signed=signed)
        self.assertEqual(lodging.present(self.company,self.day).count(),2)

    def test_expired_renew_and_stale_signed_form_rejected(self):
        key=str(uuid4());signed=lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,key)
        LodgingControl.objects.filter(company=self.company).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ValidationError):lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,key,'renew')
        self.edit(user=self.colleague,employee=self.employee)
        with self.assertRaises(ValidationError):lodging.move(self.commandant,'CHECKIN',self.a,self.day,self.employee,bed=self.bed,session=key,signed=signed)

    def test_occupied_person_place_and_future_overlap_are_atomic(self):
        self.edit()
        with self.assertRaises(ValidationError):self.edit(employee=self.employee)
        with self.assertRaises(ValidationError):self.edit(bed=self.bed2)
        self.assertEqual(lodging.present(self.company,self.day).count(),1)
        self.edit('MOVE',bed=self.bed2)
        LodgingStay.objects.create(company=self.company,camp=self.a,employee=self.employee,bed=self.bed3,arrived_on=self.day+timedelta(days=3),recorded_by=self.admin)
        with self.assertRaises(ValidationError):self.edit('MOVE',bed=self.bed3)
        self.assertEqual(lodging.present(self.company,self.day).get(employee=self.second_employee).bed,self.bed2)

    def test_survey_explanations_preserve_history_and_invalidate_on_source_change(self):
        stay=self.edit();row=self.issue(self.second_employee);signed=lodging_sync.signed_issue(self.hr,row)
        lodging_sync.explain(self.hr,self.a,self.day,signed,'Приехал вечером, заезд уточняется')
        self.assertFalse(any(r['employee'].pk==self.second_employee.pk for r in lodging_sync.issues(self.company,[self.a],self.day)))
        self.assertTrue(LodgingResolution.objects.exists())
        self.edit('UPDATE',comment='Уточнены ключи')
        self.assertEqual(self.issue(self.second_employee)['code'],'ARRIVAL')
        with self.assertRaises(ValidationError):lodging_sync.confirm_arrival(self.hr,self.a,self.day,signed,self.day)
        self.assertTrue(FactRevision.objects.filter(entity_type='personnel.lodgingresolution',actor=self.hr,direction='lodging').exists())

    def test_dates_and_camps_mismatch_and_scope(self):
        self.edit(employee=self.employee)
        self.assertEqual(self.issue(self.employee)['code'],'DATES')
        housing.move(self.hr,'TRANSFER',self.a,self.day,[self.person],target=self.b)
        self.assertEqual(self.issue(self.employee)['code'],'CAMP')
        self.assertEqual(lodging_sync.issues(self.other,[self.foreign_camp],self.day),[])

    def test_correction_by_colleague_keeps_original_author_and_stale_edit_rejected(self):
        stay=self.edit(keys_issued=True,pass_number='P1');stamp=stay.updated_at.isoformat()
        values={'arrived_on':stay.arrived_on,'departed_on':None,'planned_departure':None,'keys_issued':False,'pass_number':'P2','comment':'Уточнён пропуск'}
        lodging.correct(self.colleague,stay,values,stamp,'Исправлен пропуск')
        stay.refresh_from_db();self.assertEqual(stay.recorded_by,self.commandant);self.assertEqual(stay.pass_number,'P2')
        revision=FactRevision.objects.filter(entity_type='personnel.lodgingstay',entity_id=str(stay.pk),operation='UPDATE').latest('pk')
        self.assertEqual(revision.actor,self.colleague);self.assertEqual(revision.before['fields']['pass_number'],'P1');self.assertEqual(revision.after['fields']['pass_number'],'P2')
        with self.assertRaises(ValidationError):lodging.correct(self.commandant,stay,values,stamp,'Старая форма')
        lodging.cancel(self.colleague,stay,'Ошибочная запись',stay.updated_at.isoformat())
        self.assertFalse(lodging.present(self.company,self.day).exists());self.assertTrue(LodgingEvent.objects.filter(kind='CANCEL').exists())

    def test_role_date_and_company_boundaries(self):
        with self.assertRaises(ValidationError):self.edit(day=self.day-timedelta(days=1))
        with self.assertRaises(ValidationError):self.edit(day=timezone.localdate()+timedelta(days=1))
        self.edit(user=self.admin,day=timezone.localdate()+timedelta(days=2))
        with self.assertRaises(ValidationError):lodging.claim(self.commandant,self.a,self.day,self.employee,self.foreign_bed,str(uuid4()))
        self.assertEqual(self.client.get(reverse('personnel:lodging'),{'camp':self.foreign_camp.pk,'date':str(self.day)}).status_code,200)
        # The selector reports a scoped invalid choice without disclosing data.
        self.assertNotContains(self.client.get(reverse('personnel:lodging'),{'camp':self.foreign_camp.pk,'date':str(self.day)}),'Secret')
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(self.day)}).status_code,200)
        self.assertEqual(self.client.post(reverse('personnel:lodging_claims'),{},content_type='application/json').status_code,403)

    def test_board_catalogue_export_and_shared_sync_strip(self):
        self.edit()
        url=reverse('personnel:lodging');data={'camp':self.a.pk,'date':str(self.day)}
        response=self.client.get(url,data);self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-lodging-bed',count=3);self.assertContains(response,self.second_employee.display_name)
        self.assertContains(response,'Сверка перевахты и проживания');self.assertContains(response,'Комендант · проживание')
        self.assertEqual(self.client.get(url,{**data,'export':'xlsx'})['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertEqual(self.client.get(reverse('production:fact_history'),{'direction':'lodging'}).status_code,200)
        self.client.force_login(self.hr);page=self.client.get(reverse('personnel:housing'),data);self.assertContains(page,'data-lodging-sync-summary')
        page=self.client.get(reverse('personnel:lodging_sync'),data);self.assertContains(page,'Заезд требует подтверждения')

    def test_sync_confirmation_api_uses_source_revision_and_permission(self):
        self.edit();row=self.issue(self.second_employee)
        data={'camp':self.a.pk,'date':str(self.day),'action':'arrival','actual_date':str(self.day),'signed':lodging_sync.signed_issue(self.commandant,row)}
        self.assertEqual(self.client.post(reverse('personnel:lodging_sync'),data).status_code,403)
        self.client.force_login(self.hr);data['signed']=lodging_sync.signed_issue(self.hr,row)
        self.assertEqual(self.client.post(reverse('personnel:lodging_sync'),data).status_code,302)
        self.assertContains(self.client.post(reverse('personnel:lodging_sync'),data),'Сведения уже изменились')

    def test_building_and_bed_catalogue_create_cas_and_active_leases(self):
        data={'camp':self.a.pk,'date':str(self.day),'name':'Новый вагон','inventory_number':'W2','kind':'Жилой','is_active':'on','places':'4'}
        response=self.client.post(reverse('personnel:lodging_building_create'),data);self.assertEqual(response.status_code,302)
        building=LodgingBuilding.objects.get(name='Новый вагон');self.assertEqual(building.beds.count(),4)
        bed=building.beds.first();url=reverse('personnel:lodging_bed_edit',args=[building.pk,bed.pk]);stamp=bed.updated_at.isoformat()
        values={'date':str(self.day),'room':'3','number':'1','state':'RESERVED','note':'Для заказчика','version':stamp}
        self.assertEqual(self.client.post(url,values).status_code,302)
        self.assertContains(self.client.post(url,values),'Место уже изменено')
        bed.refresh_from_db();self.assertEqual(bed.state,'RESERVED')
        with self.assertRaises(ValidationError):self.edit(bed=bed)
        key=str(uuid4());lodging.claim(self.colleague,self.a,self.day,self.employee,self.bed,key)
        url=reverse('personnel:lodging_bed_edit',args=[self.building.pk,self.bed.pk])
        values.update(version=self.bed.updated_at.isoformat())
        self.assertContains(self.client.post(url,values),'оформляет')

    def test_shared_sync_scope_includes_multiple_camps_and_never_other_company(self):
        from .lodging_views import summary_context
        housing.move(self.hr,'ARRIVAL',self.b,self.day,[self.second_person])
        result=summary_context(self.hr,self.a,self.day,camps=[self.a,self.b])
        self.assertEqual(result['lodging_sync_count'],2)
        self.client.force_login(self.hr)
        data={'camp':self.a.pk,'date':str(self.day),'camps':[self.a.pk,self.b.pk]}
        response=self.client.get(reverse('personnel:lodging_sync_status'),data);self.assertEqual(response.json()['count'],2)
        data['camps'].append(self.foreign_camp.pk)
        self.assertEqual(self.client.get(reverse('personnel:lodging_sync_status'),data).status_code,404)

    def test_other_direction_cannot_change_lodging_or_read_its_history(self):
        self.edit()
        self.client.force_login(self.dispatcher)
        self.assertEqual(self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(self.day)}).status_code,403)
        from apps.production.history_views import visible_revisions
        self.assertFalse(visible_revisions(self.dispatcher).filter(direction='lodging').exists())
        self.assertFalse(visible_revisions(self.commandant).exclude(direction='lodging').exists())
        self.client.force_login(self.commandant)
        self.assertRedirects(self.client.get('/dashboard/'),reverse('personnel:lodging'),fetch_redirect_response=False)

    def test_historical_organization_summary_does_not_change_after_staffing_sync(self):
        from .lodging_views import place_rows
        self.second_employee.zup_organisation='Организация на дату заезда';self.second_employee.save()
        stay=self.edit()
        self.second_employee.zup_organisation='Новая организация';self.second_employee.save()
        stay.refresh_from_db();self.assertEqual(stay.organization_snapshot,'Организация на дату заезда')
        row=next(r for r in place_rows(self.company,self.a,self.day) if r['stay'])
        self.assertEqual(row['organization'],'Организация на дату заезда')

    def test_future_accommodation_does_not_allow_hr_to_confirm_future_fact(self):
        future=timezone.localdate()+timedelta(days=2)
        self.edit(user=self.admin,day=future)
        row=next(r for r in lodging_sync.issues(self.company,[self.a],future) if r['employee'].pk==self.second_employee.pk)
        with self.assertRaises(ValidationError):lodging_sync.confirm_arrival(self.hr,self.a,future,lodging_sync.signed_issue(self.hr,row),self.day)
        self.assertFalse(CampStay.objects.filter(person=self.second_person).exists())
