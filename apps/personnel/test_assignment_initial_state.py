from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import Brigade
from .models import StaffingSlot,CampStay,HousingEvent,ShiftPattern
from .rotation import rotation_rows


class AssignmentInitialStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.client.force_login(self.hr);self.url=reverse('personnel:plan_assignment_create')
        self.data={'employee':self.second_employee.pk,'planning_position':self.role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day),'assigned_until':'','pattern':''}

    def post(self,state,arrival=None,departure=None,**extra):
        return self.client.post(self.url,{**self.data,'initial-state':state,'initial-arrival_date':str(arrival) if arrival else '', 'initial-departure_date':str(departure) if departure else '',**extra})

    def assigned(self):return StaffingSlot.objects.get(planning_position=self.role)

    def roster_row(self):return next(row for row in rotation_rows(self.company,[self.a],self.day)[0] if row['person'].employee_id==self.second_employee.pk)

    def test_backdated_on_shift_is_actual_once_and_has_actor_and_day_count(self):
        past=self.day-timedelta(days=100)
        self.assertEqual(self.post('MV',past).status_code,302)
        row=self.assigned();stay=CampStay.objects.get(person=row.person)
        self.assertEqual(stay.arrived_on,past);self.assertIsNone(stay.departed_on);self.assertEqual(stay.recorded_by,self.hr)
        event=row.initial_movement;self.assertEqual(event.kind,'ARRIVAL');self.assertEqual(event.stay,stay);self.assertEqual(event.recorded_by,self.hr)
        person=self.roster_row();self.assertEqual(person['state'],'MV');self.assertTrue(person['verified']);self.assertEqual(person['state_days'],101)
        from apps.production.history_models import FactRevision
        revisions=FactRevision.objects.filter(entity_type__in=['personnel.campstay','personnel.housingevent'],actor=self.hr,reason__contains='Начальное состояние')
        self.assertEqual(revisions.count(),2)

    def test_rest_without_known_arrival_does_not_invent_stay(self):
        departure=self.day-timedelta(days=100)
        self.assertEqual(self.post('MO',departure=departure).status_code,302)
        slot=self.assigned();event=slot.initial_movement
        self.assertEqual(event.kind,'DEPARTURE');self.assertEqual(event.date,departure);self.assertEqual(event.from_camp,self.a);self.assertEqual(event.departure_destination,'REST');self.assertIsNone(event.stay_id)
        self.assertFalse(CampStay.objects.filter(person=slot.person).exists())
        row=self.roster_row();self.assertEqual(row['state'],'MO');self.assertEqual(row['state_days'],101);self.assertEqual(row['last_departure'],departure);self.assertIsNone(row['arrived_on'])
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day})
        self.assertEqual(response.context['unknown_count'],0)

    def test_rest_with_both_dates_keeps_closed_stay_and_actual_arrival_label(self):
        arrival,departure=self.day-timedelta(days=40),self.day-timedelta(days=10)
        self.assertEqual(self.post('MO',arrival,departure).status_code,302)
        slot=self.assigned();stay=CampStay.objects.get(person=slot.person)
        self.assertEqual((stay.arrived_on,stay.departed_on),(arrival,departure));self.assertEqual(HousingEvent.objects.filter(person=slot.person).count(),2)
        row=self.roster_row();self.assertEqual(row['state'],'MO');self.assertEqual(row['state_days'],11);self.assertEqual(row['arrived_on'],arrival);self.assertTrue(row['arrival_is_actual']);self.assertFalse(row['verified'])

    def test_required_dates_future_and_wrong_order_are_rejected_atomically(self):
        invalid=[('MV',None,None),('MO',None,None),('MV',self.day,self.day),('MO',self.day,self.day),('MO',self.day,self.day-timedelta(days=1)),('MV',self.day+timedelta(days=5),None),('',self.day,None)]
        for state,arrival,departure in invalid:
            response=self.post(state,arrival,departure)
            self.assertEqual(response.status_code,200);self.assertTrue(response.context['initial_form'].errors)
            self.assertFalse(StaffingSlot.objects.filter(planning_position=self.role).exists())
        self.assertFalse(HousingEvent.objects.filter(person=self.second_person).exists())

    def test_initial_state_date_cannot_follow_assignment_start(self):
        response=self.post('MV',self.day,assigned_from=str(self.day-timedelta(days=1)))
        self.assertEqual(response.status_code,200);self.assertTrue(response.context['initial_form'].errors)

    def test_existing_matching_actual_status_is_accepted_without_duplicate(self):
        before=(CampStay.objects.count(),HousingEvent.objects.count())
        response=self.post('MV',self.day,employee=self.employee.pk)
        self.assertEqual(response.status_code,302);self.assertTrue(StaffingSlot.objects.filter(planning_position=self.role).exists())
        self.assertEqual((CampStay.objects.count(),HousingEvent.objects.count()),before)

    def test_repost_has_no_duplicate_and_conflicting_opening_payload_is_rejected(self):
        past=self.day-timedelta(days=20)
        self.assertEqual(self.post('MV',past).status_code,302)
        self.assertEqual(self.post('MV',past).status_code,302)
        slot=self.assigned();self.assertEqual(CampStay.objects.filter(person=slot.person).count(),1);self.assertEqual(HousingEvent.objects.filter(person=slot.person).count(),1)
        self.assertContains(self.post('MV',past-timedelta(days=1)),'Начальное состояние уже введено')
        self.assertEqual(CampStay.objects.get(person=slot.person).arrived_on,past)

    def test_initialize_previously_empty_assignment_once_then_hide_fields_on_edit(self):
        response=self.client.post(self.url,self.data)
        self.assertEqual(response.status_code,200);self.assertIn('state',response.context['initial_form'].errors)
        self.assertEqual(self.post('MO',departure=self.day-timedelta(days=8)).status_code,302)
        slot=self.assigned();url=reverse('personnel:plan_assignment_edit',args=[slot.pk])
        self.assertNotContains(self.client.get(url),'data-assignment-initial')

    def test_event_failure_rolls_back_assignment_and_created_stay(self):
        with patch('apps.personnel.plan_assignments.HousingEvent.objects.create',side_effect=ValidationError('event failed')):
            self.assertContains(self.post('MV',self.day),'event failed')
        self.assertFalse(StaffingSlot.objects.filter(planning_position=self.role).exists());self.assertFalse(CampStay.objects.filter(person=self.second_person).exists())

    def test_normal_next_arrival_after_initial_rest_works_and_does_not_reseed(self):
        from . import housing
        departure=self.day-timedelta(days=10)
        self.assertEqual(self.post('MO',departure=departure).status_code,302)
        slot=self.assigned();housing.move(self.hr,'ARRIVAL',self.a,self.day,[slot.person])
        self.assertEqual(self.roster_row()['state'],'MV')
        self.assertEqual(HousingEvent.objects.filter(person=slot.person,kind='DEPARTURE').count(),1)

    def test_cycle_prediction_uses_known_initial_dates(self):
        pattern=ShiftPattern.objects.create(company=self.company,board=self.board,name='60/30',on_days=60,off_days=30,cycle_codes=['10']*60+['МО']*30)
        arrival=self.day-timedelta(days=20)
        self.assertEqual(self.post('MV',arrival,pattern=pattern.pk).status_code,302)
        self.assertEqual(self.roster_row()['planned_departure_display'],arrival+timedelta(days=60))
        self.assertEqual(self.roster_row()['schedule_label'],'60/30')

    def test_rest_cycle_is_anchored_to_departure_without_fake_arrival(self):
        pattern=ShiftPattern.objects.create(company=self.company,board=self.board,name='60/30',on_days=60,off_days=30,cycle_codes=['10']*60+['МО']*30)
        departure=self.day-timedelta(days=10)
        self.assertEqual(self.post('MO',departure=departure,pattern=pattern.pk).status_code,302)
        row=self.roster_row();self.assertEqual(row['planned_return'],departure+timedelta(days=30));self.assertEqual(row['state_days'],11)
        self.assertFalse(CampStay.objects.filter(person=self.assigned().person).exists())
