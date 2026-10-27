from datetime import date, datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase,TestCase
from django.urls import reverse
from django.utils import timezone
from core.fact_edit_window import can_edit_fact_day,edit_deadline,require_fact_day
from apps.production import test_dispatch as fixtures
from apps.production.models import DailyFact
from apps.production.history_models import FactRevision
from apps.production.transport_models import EquipmentDayRecord
from apps.production import transport,equipment_operations
from apps.personnel import housing
from apps.accounts.models import User,Role


def actor(code):
    return SimpleNamespace(is_superuser=False,role_id=1,role=SimpleNamespace(code=code))


class FactWindowBoundaryTests(SimpleTestCase):
    def test_fact_date_and_following_day_are_open_for_every_dispatcher_direction(self):
        day=date(2026,1,15)
        for code in ['DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER']:
            for today,allowed in [(date(2026,1,14),False),(day,True),(date(2026,1,16),True),(date(2026,1,17),False)]:
                with self.subTest(role=code,today=today):self.assertEqual(can_edit_fact_day(actor(code),day,today=today),allowed)
        self.assertTrue(can_edit_fact_day(actor('ADMIN'),day,today=date(2026,3,1)))

    def test_month_year_and_leap_day_boundaries_use_calendar_days(self):
        for day,deadline in [(date(2026,1,31),date(2026,2,1)),(date(2026,12,31),date(2027,1,1)),(date(2024,2,28),date(2024,2,29)),(date(2024,2,29),date(2024,3,1))]:
            self.assertEqual(edit_deadline(day),deadline)
            self.assertTrue(can_edit_fact_day(actor('DISPATCHER'),day,today=deadline))
            self.assertFalse(can_edit_fact_day(actor('DISPATCHER'),day,today=deadline+timedelta(days=1)))

    def test_cutoff_is_local_midnight_not_utc_midnight_or_24_hours_after_save(self):
        with timezone.override('Europe/Moscow'):
            for now,allowed in [(datetime(2026,1,16,20,59,59,tzinfo=dt_timezone.utc),True),(datetime(2026,1,16,21,0,0,tzinfo=dt_timezone.utc),False)]:
                with patch('django.utils.timezone.now',return_value=now):self.assertEqual(can_edit_fact_day(actor('DISPATCHER'),date(2026,1,15)),allowed)


class FactWindowIntegrationTests(TestCase):
    setUpTestData=classmethod(fixtures.DispatcherTests.setUpTestData.__func__)
    payload=fixtures.DispatcherTests.payload
    def setUp(self):
        self.second=User.objects.create_user(username='window-colleague',company=self.company,role=Role.objects.get(code='DISPATCHER'))
        self.second.assigned_objects.add(self.obj);self.users['SECOND']=self.second
    def post_work(self,code,amount=5,action='save'):
        data=self.payload(code,'works');data['works-0-actual_quantity']=str(amount);data['works-0-deviation_reason']=self.reason.pk;data['action']=action
        return self.client.post(self.url,data)

    def test_repeated_colleague_corrections_remain_available_even_after_confirmation(self):
        with patch('django.utils.timezone.localdate',return_value=self.day):self.assertEqual(self.post_work('DISPATCHER',5,'confirm').status_code,302)
        with patch('django.utils.timezone.localdate',return_value=self.day+timedelta(days=1)):
            self.assertEqual(self.post_work('SECOND',4).status_code,302)
            self.assertEqual(self.post_work('SECOND',3).status_code,302)
        row=DailyFact.objects.get();self.assertEqual(row.reported_by_id,self.users['DISPATCHER'].pk);self.assertEqual(row.actual_quantity,3)
        edits=FactRevision.objects.filter(entity_type='production.dailyfact',entity_id=str(row.pk),operation='UPDATE')
        self.assertEqual(edits.count(),2);self.assertEqual(set(edits.values_list('actor_id',flat=True)),{self.second.pk})

    def test_form_opened_before_cutoff_cannot_save_after_cutoff_and_admin_can_correct(self):
        with patch('django.utils.timezone.localdate',return_value=self.day):
            self.assertEqual(self.post_work('DISPATCHER').status_code,302)
            stale=self.payload('SECOND','works');stale['works-0-actual_quantity']='4';stale['works-0-deviation_reason']=self.reason.pk
        before=FactRevision.objects.count()
        with patch('django.utils.timezone.localdate',return_value=self.day+timedelta(days=2)):
            self.assertEqual(self.client.post(self.url,stale).status_code,403)
            self.assertEqual(FactRevision.objects.count(),before)
            self.assertEqual(self.post_work('ADMIN',4).status_code,302)
        row=DailyFact.objects.get();self.assertEqual(row.actual_quantity,4);self.assertEqual(row.reported_by_id,self.users['DISPATCHER'].pk)

    def test_closed_day_is_read_only_in_all_three_workspaces(self):
        with patch('django.utils.timezone.localdate',return_value=self.day+timedelta(days=2)):
            for role,path,data in [('DISPATCHER',self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'}),('PERSONNEL_DISPATCHER',reverse('personnel:housing'),{'camp':self.housing_camp.pk,'date':str(self.day)}),('MECHANIZATION_DISPATCHER',reverse('production:transport_board'),{'camp':self.housing_camp.pk,'date':str(self.day)})]:
                self.client.force_login(self.users[role]);response=self.client.get(path,data)
                self.assertEqual(response.status_code,200);self.assertFalse(response.context['fact_day_editable']);self.assertContains(response,'закрыты')
                if role=='DISPATCHER':self.assertNotContains(response,'value="confirm"')
                elif role=='PERSONNEL_DISPATCHER':self.assertNotContains(response,'data-movement-form')
                else:self.assertNotContains(response,'data-edit-machine=')

    def test_work_and_employee_reservations_close_but_status_and_release_stay_available(self):
        self.client.force_login(self.users['DISPATCHER']);session=str(uuid4())
        payload={'construction_object':self.obj.pk,'date':str(self.day),'session':session,'key':f'work:{self.work.pk}','action':'take'}
        url=reverse('production:work_claims')
        with patch('django.utils.timezone.localdate',return_value=self.day):self.assertEqual(self.client.post(url,payload,content_type='application/json').status_code,200)
        with patch('django.utils.timezone.localdate',return_value=self.day+timedelta(days=2)):
            payload['action']='renew';self.assertEqual(self.client.post(url,payload,content_type='application/json').status_code,403)
            payload['key']=f'employee:{self.employee.pk}:1';payload['action']='take';self.assertEqual(self.client.post(url,payload,content_type='application/json').status_code,403)
            self.assertEqual(self.client.get(url,payload).status_code,200)
            payload['key']=f'work:{self.work.pk}';payload['action']='release';self.assertEqual(self.client.post(url,payload,content_type='application/json').status_code,200)

    def test_transport_services_reject_changes_and_cancellation_of_closed_day(self):
        user=self.users['MECHANIZATION_DISPATCHER'];camp=self.housing_camp
        with patch('django.utils.timezone.localdate',return_value=self.day):
            transport.move(user,camp,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(user,camp,self.day))
            transport.save_day(user,camp,self.day,[self.machine.pk],'WORKED',signed=transport.snapshot(user,camp,self.day))
        arrival=self.machine.transport_events.get(kind='ARRIVAL');before=FactRevision.objects.count()
        with patch('django.utils.timezone.localdate',return_value=self.day+timedelta(days=2)):
            signed=transport.snapshot(user,camp,self.day)
            attempts=[lambda:transport.save_day(user,camp,self.day,[self.machine.pk],'CLEAR',signed=signed),lambda:transport.set_condition(user,camp,self.day,[self.machine.pk],'REPAIR',signed=signed),lambda:transport.move(user,camp,self.day,[self.machine.pk],'DEPARTURE',signed=signed),lambda:transport.cancel_event(user,arrival,'Исправление',signed=signed),lambda:transport.claim(user,camp,self.day,[self.machine.pk],'acquire',uuid4())]
            for attempt in attempts:
                with self.assertRaisesMessage(ValidationError,'закрыты'):attempt()
        self.assertEqual(FactRevision.objects.count(),before);self.assertEqual(EquipmentDayRecord.objects.get().status,'WORKED')

    def test_cutoff_rechecked_after_waiting_for_a_machine_lock(self):
        user=self.users['MECHANIZATION_DISPATCHER'];camp=self.housing_camp
        with patch('django.utils.timezone.localdate',return_value=self.day):
            transport.move(user,camp,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(user,camp,self.day))
        today=[self.day+timedelta(days=1)];real_lock=transport._lock
        def delayed_lock(*args):
            result=real_lock(*args);today[0]=self.day+timedelta(days=2);return result
        before=FactRevision.objects.count()
        with patch('django.utils.timezone.localdate',side_effect=lambda:today[0]),patch('apps.production.transport._lock',side_effect=delayed_lock):
            with self.assertRaisesMessage(ValidationError,'закрыты'):
                transport.save_day(user,camp,self.day,[self.machine.pk],'WORKED',signed=transport.snapshot(user,camp,self.day))
        self.assertFalse(EquipmentDayRecord.objects.exists());self.assertEqual(FactRevision.objects.count(),before)

    def test_personnel_cannot_rewrite_old_arrival_but_can_record_todays_departure(self):
        user=self.users['PERSONNEL_DISPATCHER'];person=self.housing_person;stay=person.stays.get()
        stay.arrived_on=self.day-timedelta(days=10);stay.save()
        today=self.day+timedelta(days=2)
        with patch('django.utils.timezone.localdate',return_value=today):
            with self.assertRaisesMessage(ValidationError,'закрыты'):housing.move(user,'DEPARTURE',self.housing_camp,self.day,[person])
            housing.move(user,'DEPARTURE',self.housing_camp,today,[person])
            stay.refresh_from_db();self.assertEqual(stay.departed_on,today)
            with self.assertRaisesMessage(ValidationError,'закрыты'):housing.cancel_stay(user,stay,'Удалить прежний период',stay.updated_at.isoformat())

    def test_personnel_import_cannot_seed_closed_actual_dates(self):
        from apps.personnel.models import Board,StaffingGroup,StaffingSlot,Person
        board=Board.objects.create(company=self.company,name='Импорт')
        group=StaffingGroup.objects.create(company=self.company,board=board,position='Монтажник')
        person=Person.objects.create(company=self.company,full_name='Сотрудник из архива')
        slot=StaffingSlot.objects.create(company=self.company,group=group,person=person,housing_camp=self.housing_camp,camp=self.housing_camp.name,status='МВ',arrival_date=self.day-timedelta(days=10))
        with patch('django.utils.timezone.localdate',return_value=self.day):
            with self.assertRaisesMessage(ValidationError,'закрыты'):housing.bootstrap_legacy(self.company,StaffingSlot.objects.filter(pk=slot.pk),user=self.users['PERSONNEL_DISPATCHER'])
            self.assertFalse(person.stays.exists())
            housing.bootstrap_legacy(self.company,StaffingSlot.objects.filter(pk=slot.pk),user=self.users['ADMIN'])
            self.assertTrue(person.stays.exists())

    def test_old_assignment_form_has_the_same_cutoff(self):
        from apps.production.models import EquipmentAssignment
        user=self.users['MECHANIZATION_DISPATCHER']
        with patch('django.utils.timezone.localdate',return_value=self.day):
            assignment=equipment_operations.create_assignment(user,EquipmentAssignment(company=self.company,equipment=self.machine,construction_object=self.obj,start_date=self.day,work=self.work,activity='Работа'))
        with patch('django.utils.timezone.localdate',return_value=self.day+timedelta(days=2)):
            for attempt in [lambda:equipment_operations.change_state(user,assignment,1,self.day,'REPAIR','Ремонт'),lambda:equipment_operations.close_assignment(user,assignment,1,self.day),lambda:equipment_operations.cancel_assignment(user,assignment,1,'Ошибка')]:
                with self.assertRaisesMessage(ValidationError,'закрыты'):attempt()
