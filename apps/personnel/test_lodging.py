import json
from datetime import timedelta
from io import BytesIO
from uuid import uuid4
from unittest.mock import patch
from openpyxl import load_workbook
from django.test import TestCase,Client
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User,Role
from apps.resources.models import Employee
from apps.production.history_models import FactRevision
from . import test_housing as housing_fixtures
from .models import LodgingOrganization,LodgingGuest,LodgingBuilding,LodgingBed,LodgingStay,LodgingEvent,LodgingControl,LodgingPresence,LodgingResolution,CampStay
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
    def activate_editor(self,user=None,camp=None):
        user=user or self.commandant;camp=camp or self.a
        LodgingPresence.objects.filter(company=self.company,camp=camp).delete()
        LodgingControl.objects.filter(company=self.company,resource='camp',resource_id=camp.pk).update(locked_by=None,session=None,expires_at=None)
        session=str(uuid4());state=lodging.presence(user,camp,session)
        self.assertTrue(state['editor'])
        return session
    def edit(self,kind='CHECKIN',*,user=None,employee=None,bed=None,day=None,**kwargs):
        user=user or self.commandant;employee=employee or self.second_employee;day=day or self.day
        bed=(bed or self.bed) if kind in {'CHECKIN','MOVE'} else None
        session=self.activate_editor(user);signed=lodging.claim(user,self.a,day,employee,bed,session)
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
        self.assertEqual(lodging.present(self.company,self.day-timedelta(days=1)).get(employee=self.employee).bed,self.bed2)

    def test_departures_independent_and_discrepancies_resolve(self):
        self.edit(user=self.admin,employee=self.employee,day=self.stay.arrived_on)
        housing.move(self.hr,'DEPARTURE',self.a,self.day,[self.person])
        self.assertEqual(self.issue(self.employee)['code'],'DEPARTED_OPEN')
        self.edit('CHECKOUT',employee=self.employee)
        self.assertFalse(any(r['employee'].pk==self.employee.pk for r in lodging_sync.issues(self.company,[self.a],self.day)))
        self.edit();housing.move(self.hr,'ARRIVAL',self.a,self.day,[self.second_person])
        self.edit('CHECKOUT');self.assertEqual(self.issue(self.second_employee)['code'],'AFTER_CHECKOUT')
        self.assertTrue(housing.present_stays(self.company,self.day).filter(person=self.second_person).exists())

    def test_one_editor_per_location_and_live_viewers(self):
        key=self.activate_editor(self.commandant);signed=lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,key)
        viewer=str(uuid4());state=lodging.presence(self.colleague,self.a,viewer)
        self.assertFalse(state['editor']);self.assertEqual(state['editor_name'],'commandant')
        with self.assertRaises(ValidationError):lodging.claim(self.colleague,self.a,self.day,self.employee,self.bed2,str(uuid4()))
        with self.assertRaises(ValidationError):lodging.claim(self.colleague,self.a,self.day,self.second_employee,self.bed,str(uuid4()))
        self.assertIn('commandant-two',lodging.presence(self.commandant,self.a,key)['viewers'])
        # Another tab of the same operator keeps the same user-level editing session.
        lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,str(uuid4()))
        lodging.move(self.commandant,'CHECKIN',self.a,self.day,self.employee,bed=self.bed,session=key,signed=signed)
        LodgingPresence.objects.filter(user=self.commandant,camp=self.a).update(expires_at=timezone.now()-timedelta(seconds=1))
        LodgingControl.objects.filter(company=self.company).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertTrue(lodging.presence(self.colleague,self.a,viewer)['editor'])

    def test_expired_renew_and_stale_signed_form_rejected(self):
        key=self.activate_editor();signed=lodging.claim(self.commandant,self.a,self.day,self.employee,self.bed,key)
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
        self.activate_editor(self.colleague)
        lodging.correct(self.colleague,stay,values,stamp,'Исправлен пропуск')
        stay.refresh_from_db();self.assertEqual(stay.recorded_by,self.commandant);self.assertEqual(stay.pass_number,'P2')
        revision=FactRevision.objects.filter(entity_type='personnel.lodgingstay',entity_id=str(stay.pk),operation='UPDATE').latest('pk')
        self.assertEqual(revision.actor,self.colleague);self.assertEqual(revision.before['fields']['pass_number'],'P1');self.assertEqual(revision.after['fields']['pass_number'],'P2')
        self.activate_editor(self.commandant)
        with self.assertRaises(ValidationError):lodging.correct(self.commandant,stay,values,stamp,'Старая форма')
        self.activate_editor(self.colleague)
        lodging.cancel(self.colleague,stay,'Ошибочная запись',stay.updated_at.isoformat())
        self.assertFalse(lodging.present(self.company,self.day).exists());self.assertTrue(LodgingEvent.objects.filter(kind='CANCEL').exists())

    def test_update_without_removed_planned_departure_field_preserves_old_value(self):
        planned = self.day + timedelta(days=14)
        stay = self.edit(planned_departure=planned)
        self.edit('UPDATE', employee=self.second_employee, pass_number='P-20')
        stay.refresh_from_db()
        self.assertEqual(stay.planned_departure, planned)
        self.assertEqual(stay.pass_number, 'P-20')

    def test_movement_success_links_open_occupied_and_freed_places(self):
        def submit(kind, bed=None):
            session = self.activate_editor()
            signed = lodging.claim(self.commandant, self.a, self.day, self.second_employee, bed, session)
            payload = {
                'camp': self.a.pk, 'date': str(self.day), 'kind': kind,
                'employee': self.second_employee.pk, 'session': session, 'signed': signed,
            }
            if bed:
                payload.update({'building': bed.building_id, 'bed': bed.pk})
            return self.client.post(reverse('personnel:lodging'), payload, follow=True)

        checkin = submit('CHECKIN', self.bed)
        self.assertContains(checkin, f'Заселение сотрудника <strong>{self.second_employee.display_name}</strong> выполнено успешно')
        self.assertContains(checkin, 'Показать место:')
        self.assertContains(checkin, 'Вагон 1 · место 1')
        self.assertContains(checkin, f'focus_bed={self.bed.pk}')

        moved = submit('MOVE', self.bed2)
        self.assertContains(moved, 'Переселение сотрудника')
        self.assertContains(moved, 'Старое место:')
        self.assertContains(moved, 'Новое место:')
        self.assertContains(moved, f'focus_bed={self.bed.pk}')
        self.assertContains(moved, f'focus_bed={self.bed2.pk}')

        checkout = submit('CHECKOUT')
        self.assertContains(checkout, 'Выселение сотрудника')
        self.assertContains(checkout, 'Освободившееся место:')
        self.assertContains(checkout, f'focus_bed={self.bed2.pk}')
        self.assertContains(checkout, f'data-lodging-building="{self.building.pk}"')

    def test_role_date_and_company_boundaries(self):
        historical=self.day-timedelta(days=30)
        stay=self.edit(day=historical)
        self.assertEqual(stay.arrived_on,historical)
        self.edit('CHECKOUT',day=historical+timedelta(days=1))
        future=timezone.localdate()+timedelta(days=2)
        stay=self.edit(day=future)
        self.assertEqual(stay.arrived_on,future)
        page=self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(historical)})
        self.assertContains(page,'Ввод доступен')
        self.assertNotContains(page,'Оформить проживание')
        self.assertNotContains(page,'href="#places">Места</a>')
        self.assertNotContains(page,'закрыты с')
        places=self.client.get(reverse('personnel:lodging_building_places',args=[self.building.pk]),{'camp':self.a.pk,'date':str(historical)})
        self.assertContains(places,'Выселить')
        with self.assertRaises(ValidationError):lodging.claim(self.commandant,self.a,self.day,self.employee,self.foreign_bed,str(uuid4()))
        self.assertEqual(self.client.get(reverse('personnel:lodging'),{'camp':self.foreign_camp.pk,'date':str(self.day)}).status_code,200)
        # The selector reports a scoped invalid choice without disclosing data.
        self.assertNotContains(self.client.get(reverse('personnel:lodging'),{'camp':self.foreign_camp.pk,'date':str(self.day)}),'Secret')
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(self.day)}).status_code,200)
        self.assertEqual(self.client.post(reverse('personnel:lodging_claims'),{},content_type='application/json').status_code,403)

    def test_recorded_dates_never_delay_current_checkin_move_or_checkout(self):
        future=self.day+timedelta(days=30);past=self.day-timedelta(days=30)
        stay=self.edit(day=future)
        self.assertEqual(stay.arrived_on,future)
        self.assertEqual(lodging.present(self.company,past).get(employee=self.second_employee).bed,self.bed)

        page=self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(past)})
        self.assertNotContains(page,'>На дату<')
        self.assertNotContains(page,'data-fact-shortcuts')
        self.assertContains(page,'<small>Сегодня</small>',html=True)
        places=self.client.get(reverse('personnel:lodging_building_places',args=[self.building.pk]),{'camp':self.a.pk,'date':str(past)})
        self.assertContains(places,self.second_employee.display_name)

        moved=self.edit('MOVE',bed=self.bed2,day=past)
        stay.refresh_from_db()
        self.assertEqual(stay.departed_on,past)
        self.assertEqual(moved.arrived_on,past)
        self.assertEqual(lodging.present(self.company,future).get(employee=self.second_employee).bed,self.bed2)

        self.edit('CHECKOUT',day=future+timedelta(days=1))
        self.assertFalse(lodging.present(self.company,past).filter(employee=self.second_employee).exists())
        self.assertTrue(lodging.available_beds(self.company,self.a,past).filter(pk=self.bed2.pk).exists())
        self.assertEqual(list(LodgingEvent.objects.filter(employee=self.second_employee).values_list('date',flat=True)),[future+timedelta(days=1),future,past])

    def test_board_catalogue_export_and_shared_sync_strip(self):
        self.edit()
        url=reverse('personnel:lodging');data={'camp':self.a.pk,'date':str(self.day)}
        response=self.client.get(url,data);self.assertEqual(response.status_code,200)
        self.assertContains(response,'class="lodging-primary-row"')
        self.assertContains(response,'class="lodging-secondary-row"')
        self.assertContains(response,'data-lodging-save',count=1)
        self.assertContains(response,'>Сохранить</button>')
        self.assertNotContains(response,'data-lodging-take')
        self.assertNotContains(response,'Начать оформление')
        self.assertNotContains(response,'data-lodging-details disabled')
        self.assertNotContains(response,'data-lodging-bed');self.assertContains(response,'data-lazy-url')
        places=self.client.get(reverse('personnel:lodging_building_places',args=[self.building.pk]),data)
        self.assertContains(places,'data-lodging-bed',count=3);self.assertContains(places,self.second_employee.display_name)
        self.assertContains(places,'data-resident-label=',count=3)
        self.assertContains(places,'data-lodging-room',count=2);self.assertContains(places,'Комната 2')
        self.assertContains(response,'data-lodging-expand="all"');self.assertContains(response,'data-lodging-expand="none"')
        history=self.client.get(reverse('personnel:lodging_event_history'),data);self.assertEqual(history.status_code,200)
        self.assertContains(response,'Сверка перевахты и проживания');self.assertContains(response,'Комендант · проживание')
        self.assertEqual(self.client.get(url,{**data,'export':'xlsx'})['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.assertEqual(self.client.get(reverse('production:fact_history'),{'direction':'lodging'}).status_code,200)
        self.client.force_login(self.hr);page=self.client.get(reverse('personnel:housing'),data);self.assertContains(page,'data-lodging-sync-summary')
        page=self.client.get(reverse('personnel:lodging_sync'),data);self.assertContains(page,'Заезд требует подтверждения')

    def test_board_has_linked_multiple_building_and_bed_filters(self):
        second_building=LodgingBuilding.objects.create(company=self.company,camp=self.a,name='Корпус 2',description='Северный')
        second_bed=LodgingBed.objects.create(company=self.company,building=second_building,room='А',number='10')
        url=reverse('personnel:lodging')

        page=self.client.get(url,{'camp':self.a.pk,'buildings':[second_building.pk]})
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'name="buildings"',count=1)
        self.assertContains(page,'name="beds"',count=1)
        self.assertContains(page,'data-remote-building-field="buildings"')
        self.assertContains(page,'multiple')
        self.assertEqual([group['building'].pk for group in page.context['groups']],[second_building.pk])
        self.assertEqual(page.context['stats']['FREE'],1)

        filtered=self.client.get(url,{'camp':self.a.pk,'buildings':[self.building.pk],'beds':[self.bed2.pk]})
        self.assertEqual([group['building'].pk for group in filtered.context['groups']],[self.building.pk])
        self.assertEqual(filtered.context['groups'][0]['counts']['TOTAL'],1)
        self.assertIn(f'beds={self.bed2.pk}',filtered.context['groups'][0]['load_url'])
        places=self.client.get(reverse('personnel:lodging_building_places',args=[self.building.pk]),{'camp':self.a.pk,'beds':[self.bed2.pk]})
        self.assertContains(places,'data-lodging-bed',count=1)
        self.assertContains(places,f'id="lodging-bed-{self.bed2.pk}"')
        self.assertNotContains(places,f'id="lodging-bed-{self.bed.pk}"')
        export=self.client.get(url,{'camp':self.a.pk,'beds':[self.bed2.pk],'export':'xlsx'})
        self.assertEqual(export['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        sheet=load_workbook(BytesIO(export.content),read_only=True).active
        self.assertEqual(sheet.max_row,2)
        self.assertEqual(sheet.cell(2,7).value,self.bed2.number)

        search_url=reverse('personnel:lodging_filter_beds')
        results=self.client.get(search_url,{'camp':self.a.pk,'building':[second_building.pk],'q':'А 10'}).json()['items']
        self.assertEqual([item['id'] for item in results],[second_bed.pk])
        self.assertIn('Корпус 2',results[0]['label'])
        self.assertEqual(self.client.get(search_url,{'camp':self.a.pk,'building':[self.foreign_building.pk]}).json()['items'],[])

    def test_commandant_adds_organization_and_manages_temporary_resident(self):
        organization_response=self.client.post(reverse('personnel:lodging_quick_organization'),{
            'name':'ООО Северный Монтаж','short_name':'Северный Монтаж','note':'Добавлено при заселении'})
        self.assertEqual(organization_response.status_code,201)
        organization=LodgingOrganization.objects.get(pk=organization_response.json()['id'])
        self.assertEqual(organization.status,'REVIEW');self.assertEqual(organization.created_by,self.commandant)
        duplicate=self.client.post(reverse('personnel:lodging_quick_organization'),{'name':'  ооо северный монтаж  '})
        self.assertEqual(duplicate.status_code,400)
        directory=self.client.get(reverse('personnel:lodging_organizations'));self.assertContains(directory,organization.name);self.assertNotContains(directory,'>Подтвердить</button>')

        def submit(kind,bed=None,guest=None):
            session=self.activate_editor();payload={'camp':self.a.pk,'date':str(self.day),'kind':kind,'temporary':'on','session':session,'signed':''}
            if guest:payload['guest']=guest.pk
            else:payload.update({'guest_full_name':'Сидоров Семён Сергеевич','guest_organization':organization.pk,'guest_position_text':'Сварщик','guest_employee_number':'ВР-17','guest_note':'Командировка'})
            if bed:payload.update({'building':bed.building_id,'bed':bed.pk})
            return self.client.post(reverse('personnel:lodging'),payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        checkin=submit('CHECKIN',self.bed)
        self.assertEqual(checkin.status_code,200,checkin.content)
        guest=LodgingGuest.objects.get();stay=LodgingStay.objects.get(guest=guest,departed_on=None)
        self.assertIsNone(stay.employee_id);self.assertEqual(stay.organization_snapshot,'Северный Монтаж');self.assertEqual(stay.position_snapshot,'Сварщик')
        self.assertEqual(checkin.json()['resident_kind'],'guest')
        places=self.client.get(reverse('personnel:lodging_building_places',args=[self.building.pk]),{'camp':self.a.pk})
        self.assertContains(places,guest.full_name);self.assertContains(places,'Временный проживающий');self.assertContains(places,'data-guest=')

        moved=submit('MOVE',self.bed2,guest);self.assertEqual(moved.status_code,200,moved.content)
        self.assertEqual(lodging.present(self.company).get(guest=guest).bed,self.bed2)
        checkout=submit('CHECKOUT',guest=guest);self.assertEqual(checkout.status_code,200,checkout.content)
        self.assertFalse(lodging.present(self.company).filter(guest=guest).exists())
        self.assertEqual(list(LodgingEvent.objects.filter(guest=guest).values_list('kind',flat=True)),['CHECKOUT','MOVE','CHECKIN'])

    def test_composition_report_groups_registered_and_temporary_residents_and_exports_two_sheets(self):
        self.edit()
        organization=LodgingOrganization.objects.create(company=self.company,name='Подрядчик',status='VERIFIED',created_by=self.admin)
        guest=LodgingGuest.objects.create(company=self.company,full_name='Временный Работник',organization=organization,position_name='Сварщик',created_by=self.commandant)
        self.edit(employee=self.employee,bed=self.bed2)
        self.edit('CHECKOUT',employee=self.employee)
        session=self.activate_editor();lodging.move(self.commandant,'CHECKIN',self.a,self.day,guest=guest,bed=self.bed2,session=session)
        params={'date':str(self.day),'mode':'FACTUAL','view':'POSITION','camps':[self.a.pk]}
        response=self.client.get(reverse('personnel:lodging_composition'),params)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Состав проживающих');self.assertContains(response,'Сварщик');self.assertContains(response,guest.full_name)
        self.assertContains(response,'data-lodging-nav-active="composition"')
        self.assertContains(response,'aria-current="page"',count=1)
        navigation=response.content.decode()
        self.assertLess(navigation.index('>Проживание</a>'),navigation.index('>Состояние на дату</a>'))
        self.assertEqual(response.context['stats']['GUEST'],1)
        export=self.client.get(reverse('personnel:lodging_composition'),{**params,'export':'xlsx'})
        book=load_workbook(BytesIO(export.content),read_only=True)
        self.assertEqual(book.sheetnames,['Сводка','Список сотрудников'])
        self.assertEqual(book['Список сотрудников'].max_row,3)
        book.close()
        default_mode=self.client.get(reverse('personnel:lodging_composition'),{
            'date':str(self.day),'view':'POSITION','camps':[self.a.pk]})
        self.assertTrue(default_mode.context['form'].is_valid())
        self.assertEqual(default_mode.context['mode'],'FACTUAL')
        self.assertContains(default_mode,'Расчёт выполнен по указанным фактическим датам.')

    def test_commandant_can_load_company_employee_choices(self):
        search=reverse('personnel:employee_search')
        params={'scope':'lodging','kind':'CHECKIN','camp':self.a.pk,'date':str(self.day)}
        LodgingStay.objects.create(company=self.company,camp=self.a,employee=self.employee,bed=self.bed3,arrived_on=self.day+timedelta(days=3),recorded_by=self.admin)
        future=self.client.get(search,{**params,'q':self.employee.employee_number}).json()['items']
        self.assertEqual([item['id'] for item in future],[self.employee.pk])
        self.assertTrue(future[0]['disabled'])
        self.assertIn('уже заселён:',future[0]['text'])
        response=self.client.get(search,{**params,'q':self.second_employee.employee_number})
        self.assertEqual(response.status_code,200)
        self.assertEqual([item['id'] for item in response.json()['items']],[self.second_employee.pk])
        self.edit(employee=self.second_employee)
        occupied=self.client.get(search,{**params,'q':self.second_employee.employee_number}).json()['items']
        self.assertEqual([item['id'] for item in occupied],[self.second_employee.pk])
        self.assertTrue(occupied[0]['disabled'])
        self.assertIn('уже заселён:',occupied[0]['text'])
        self.assertIn(str(self.a),occupied[0]['text'])
        self.assertIn(str(self.bed),occupied[0]['text'])
        not_lodged=self.client.get(search,{**params,'kind':'CHECKOUT','q':self.employee.employee_number}).json()['items']
        self.assertEqual([item['id'] for item in not_lodged],[self.employee.pk])
        self.assertFalse(not_lodged[0].get('disabled',False))
        self.second_employee.is_active=False;self.second_employee.save(update_fields=['is_active','updated_at'])
        self.assertEqual(self.client.get(search,{**params,'q':self.second_employee.employee_number}).json()['items'],[])
        checkout=self.client.get(search,{**params,'kind':'CHECKOUT','q':self.second_employee.employee_number})
        self.assertEqual([item['id'] for item in checkout.json()['items']],[self.second_employee.pk])
        self.assertFalse(checkout.json()['items'][0].get('disabled',False))
        self.assertEqual(self.client.get(search,{**params,'q':self.second_employee.employee_number}).json()['items'],[])
        foreign=self.client.get(search,{**params,'q':'Secret'})
        self.assertEqual(foreign.status_code,200)
        self.assertEqual(foreign.json()['items'],[])

    def test_lodging_employee_widget_sends_action_camp_and_day(self):
        response=self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(self.day)})
        attrs=response.context['form'].fields['employee'].widget.attrs
        self.assertEqual(attrs['data-remote-scope'],'lodging')
        self.assertEqual(attrs['data-remote-kind-field'],'kind')
        self.assertEqual(attrs['data-remote-camp-field'],'camp')
        self.assertEqual(attrs['data-remote-date-field'],'date')
        self.assertEqual(attrs['data-remote-preserve-selection-fields'],'kind,date')
        self.assertNotIn('data-remote-include-archived',attrs)

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
        viewer=str(uuid4());state=lodging.presence(self.colleague,self.a,viewer);self.assertFalse(state['editor'])
        url=reverse('personnel:lodging_bed_edit',args=[self.building.pk,self.bed.pk])
        values.update(version=self.bed.updated_at.isoformat())
        other=Client();other.force_login(self.colleague)
        self.assertEqual(other.post(url,values).status_code,403)

    def test_board_names_editor_and_viewers_then_promotes_after_timeout(self):
        scope={'camp':self.a.pk,'date':str(self.day)}
        page=self.client.get(reverse('personnel:lodging'),scope)
        self.assertContains(page,'Вы редактируете')
        other=Client();other.force_login(self.colleague)
        viewer_page=other.get(reverse('personnel:lodging'),scope)
        self.assertContains(viewer_page,'Только просмотр')
        self.assertContains(viewer_page,'commandant')
        self.assertNotContains(viewer_page,'data-lodging-movement')
        denied=other.post(reverse('personnel:lodging_claims'),json.dumps({'camp':self.a.pk,'date':str(self.day),'employee':self.second_employee.pk,'bed':self.bed.pk,'session':viewer_page.context['presence_session']}),content_type='application/json')
        self.assertEqual(denied.status_code,409);self.assertIn('редактирует commandant',denied.json()['error'])
        page=self.client.get(reverse('personnel:lodging'),scope)
        self.assertContains(page,'commandant-two')
        LodgingPresence.objects.filter(user=self.commandant,camp=self.a).update(expires_at=timezone.now()-timedelta(seconds=1))
        LodgingControl.objects.filter(company=self.company,resource='camp',resource_id=self.a.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        promoted=other.post(reverse('personnel:lodging_presence'),data='{"camp":%s,"session":"%s"}'%(self.a.pk,uuid4()),content_type='application/json')
        self.assertTrue(promoted.json()['editor'])

    def test_shared_sync_scope_includes_multiple_camps_and_never_other_company(self):
        from .lodging_views import summary_context
        housing.move(self.hr,'ARRIVAL',self.b,self.day,[self.second_person])
        for camp in (self.a,self.b):
            state=lodging_sync.state_for(self.company,camp,self.day);state.status='QUEUED';state.save(update_fields=['status','updated_at']);lodging_sync.calculate_state(state.pk)
        result=summary_context(self.hr,self.a,self.day,camps=[self.a,self.b])
        self.assertEqual(result['lodging_sync_count'],2)
        self.client.force_login(self.hr)
        data={'camp':self.a.pk,'date':str(self.day),'camps':[self.a.pk,self.b.pk]}
        response=self.client.get(reverse('personnel:lodging_sync_status'),data);self.assertEqual(response.json()['count'],2)
        data['camps'].append(self.foreign_camp.pk)
        self.assertEqual(self.client.get(reverse('personnel:lodging_sync_status'),data).status_code,404)

    def test_board_uses_saved_sync_state_and_aggregate_counts(self):
        from .lodging_views import summary_context
        with patch('apps.personnel.lodging_views.lodging_sync.issues',side_effect=AssertionError('full sync must stay off the board')):
            summary=summary_context(self.commandant,self.a,self.day)
        self.assertFalse(summary['lodging_sync_current'])
        with patch('apps.personnel.lodging_views.place_rows',side_effect=AssertionError('default board must use aggregates')):
            page=self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk})
        self.assertEqual(page.status_code,200);self.assertIn('Server-Timing',page)
        html=page.content.decode();self.assertLess(html.index('aria-label="Учёт проживания"'),html.index('data-lodging-result'));self.assertLess(html.index('data-lodging-result'),html.index('id="movement"'))

    def test_ajax_save_is_one_request_and_returns_partial_updates(self):
        page=self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk})
        form=page.context['form'];day=page.context['day']
        response=self.client.post(reverse('personnel:lodging'),{'camp':self.a.pk,'kind':'CHECKIN','date':str(day),
            'employee':self.second_employee.pk,'building':self.building.pk,'bed':self.bed.pk,
            'session':form.initial['session'],'signed':'','key_number':'12','keys_issued':'on'},HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200);payload=response.json();self.assertTrue(payload['ok'])
        self.assertEqual(payload['stay']['bed'],self.bed.pk);self.assertIn(self.building.pk,payload['affected_buildings'])
        self.assertEqual(lodging.present(self.company).get(employee=self.second_employee).key_number,'12')

    def test_historical_report_has_portal_and_factual_modes(self):
        self.edit(employee=self.second_employee,day=self.day)
        url=reverse('personnel:lodging_report')
        portal=self.client.get(url,{'camp':self.a.pk,'date':str(timezone.localdate()),'mode':'PORTAL'})
        self.assertEqual(portal.status_code,200);self.assertContains(portal,'По состоянию портала')
        self.assertContains(portal,'data-lodging-nav-active="report"')
        self.assertContains(portal,self.second_employee.display_name)
        factual=self.client.get(url,{'camp':self.a.pk,'date':str(self.day),'mode':'FACTUAL'})
        self.assertContains(factual,'По фактическим датам');self.assertContains(factual,self.second_employee.display_name)
        for view,title in [('ORGANIZATION','По организациям'),('POSITION','По должностям')]:
            grouped=self.client.get(url,{'camp':self.a.pk,'date':str(self.day),'mode':'FACTUAL','view':view})
            self.assertEqual(grouped.status_code,200);self.assertEqual(grouped.context['view'],view)
            self.assertContains(grouped,title);self.assertContains(grouped,self.second_employee.display_name)
            self.assertEqual(sum(group['total'] for group in grouped.context['grouped_rows']),1)
        export=self.client.get(url,{'camp':self.a.pk,'date':str(self.day),'mode':'FACTUAL','export':'xlsx'})
        self.assertEqual(export['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

    def test_presence_does_not_take_company_wide_mutex(self):
        with patch('apps.personnel.lodging.mutex',side_effect=AssertionError('presence must be camp scoped')):
            state=lodging.presence(self.commandant,self.a,str(uuid4()))
        self.assertTrue(state['editor'])

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
