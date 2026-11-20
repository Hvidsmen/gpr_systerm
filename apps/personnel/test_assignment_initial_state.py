from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.resources.models import Brigade
from .models import CAMP_STAY_OVERLAP_ERROR,StaffingSlot,CampStay,HousingEvent,ShiftPattern
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

    def roster_row(self,day=None):return next(row for row in rotation_rows(self.company,[self.a],day or self.day)[0] if row['person'].employee_id==self.second_employee.pk)

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

    def test_submitted_assignment_start_cannot_backdate_new_assignment(self):
        response=self.post('MV',self.day,assigned_from=str(self.day-timedelta(days=1)))
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.assigned().assigned_from,timezone.localdate())

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

    def test_old_assignment_can_record_later_arrival_from_rest(self):
        assigned_from=self.day-timedelta(days=90)
        slot=StaffingSlot.objects.create(
            company=self.company,group=self.group,person=self.second_person,
            planning_position=self.role,construction_object=self.obj,housing_camp=self.a,
            assigned_from=assigned_from,assigned_by=self.hr,
        )
        HousingEvent.objects.create(
            company=self.company,person=self.second_person,date=self.day-timedelta(days=10),
            kind='DEPARTURE',from_camp=self.a,departure_destination='REST',recorded_by=self.hr,
        )
        url=reverse('personnel:plan_assignment_edit',args=[slot.pk])
        page=self.client.get(url)
        self.assertEqual(page.context['initial_form'].fields['arrival_date'].widget.attrs['max'],str(timezone.localdate()))
        response=self.client.post(url,{
            **self.data,'version':slot.updated_at.isoformat(),
            'initial-state':'MV','initial-arrival_date':str(self.day),'initial-departure_date':'',
        })
        self.assertEqual(response.status_code,302)
        slot.refresh_from_db()
        self.assertEqual(slot.initial_movement.kind,'ARRIVAL')
        self.assertEqual(slot.initial_movement.date,self.day)
        self.assertTrue(CampStay.objects.filter(person=self.second_person,camp=self.a,arrived_on=self.day,departed_on=None).exists())

    def test_existing_closed_stay_can_be_changed_from_rest_to_on_shift(self):
        assigned_from=self.day-timedelta(days=90)
        slot=StaffingSlot.objects.create(
            company=self.company,group=self.group,person=self.second_person,
            planning_position=self.role,construction_object=self.obj,housing_camp=self.a,
            assigned_from=assigned_from,assigned_by=self.hr,
        )
        old_arrival=self.day-timedelta(days=117)
        old_departure=self.day-timedelta(days=15)
        old_stay=CampStay.objects.create(
            company=self.company,person=self.second_person,camp=self.a,
            arrived_on=old_arrival,departed_on=old_departure,recorded_by=self.hr,
        )
        HousingEvent.objects.create(
            company=self.company,person=self.second_person,date=old_arrival,
            kind='ARRIVAL',to_camp=self.a,stay=old_stay,recorded_by=self.hr,
        )
        HousingEvent.objects.create(
            company=self.company,person=self.second_person,date=old_departure,
            kind='DEPARTURE',from_camp=self.a,stay=old_stay,
            departure_destination='REST',recorded_by=self.hr,
        )

        response=self.client.post(reverse('personnel:plan_assignment_edit',args=[slot.pk]),{
            **self.data,'version':slot.updated_at.isoformat(),
            'initial-state':'MV','initial-arrival_date':str(self.day),'initial-departure_date':'',
        })

        self.assertEqual(response.status_code,302)
        row=self.roster_row()
        self.assertEqual(row['state'],'MV')
        self.assertTrue(row['verified'])
        self.assertEqual(row['arrived_on'],self.day)
        self.assertTrue(CampStay.objects.filter(
            person=self.second_person,camp=self.a,arrived_on=self.day,departed_on=None,
        ).exists())

    def test_admin_can_record_initial_arrival_for_testing(self):
        HousingEvent.objects.create(
            company=self.company,person=self.second_person,date=self.day-timedelta(days=10),
            kind='DEPARTURE',from_camp=self.a,departure_destination='REST',recorded_by=self.hr,
        )
        self.client.force_login(self.admin)
        response=self.post('MV',self.day)
        self.assertEqual(response.status_code,302)
        slot=self.assigned()
        self.assertEqual(slot.initial_movement.kind,'ARRIVAL')
        self.assertEqual(slot.initial_movement.recorded_by,self.admin)
        self.assertTrue(CampStay.objects.filter(person=self.second_person,camp=self.a,arrived_on=self.day,departed_on=None).exists())

    def test_admin_acting_as_hr_head_can_record_initial_arrival_for_testing(self):
        from apps.accounts.models import Role
        self.hr.role=Role.objects.get(code='HR_HEAD');self.hr.save(update_fields=['role'])
        HousingEvent.objects.create(
            company=self.company,person=self.second_person,date=self.day-timedelta(days=10),
            kind='DEPARTURE',from_camp=self.a,departure_destination='REST',recorded_by=self.hr,
        )
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('accounts:impersonation_start',args=[self.hr.pk])).status_code,302)

        response=self.post('MV',self.day)

        self.assertEqual(response.status_code,302)
        self.assertIn('date='+self.day.isoformat(),response.url)
        slot=self.assigned()
        self.assertEqual(slot.initial_movement.kind,'ARRIVAL')
        self.assertTrue(CampStay.objects.filter(person=self.second_person,camp=self.a,arrived_on=self.day,departed_on=None).exists())

    def test_event_failure_rolls_back_assignment_and_created_stay(self):
        with patch('apps.personnel.plan_assignments.HousingEvent.objects.create',side_effect=ValidationError('event failed')):
            self.assertContains(self.post('MV',self.day),'event failed')
        self.assertFalse(StaffingSlot.objects.filter(planning_position=self.role).exists());self.assertFalse(CampStay.objects.filter(person=self.second_person).exists())

    def test_overlapping_stay_error_links_to_employee_in_dispatcher(self):
        existing=CampStay.objects.create(company=self.company,person=self.second_person,camp=self.b,
            arrived_on=self.day-timedelta(days=10),comment='Текущее размещение')
        with patch('apps.personnel.plan_assignments.save_assignment',side_effect=ValidationError(CAMP_STAY_OVERLAP_ERROR)):
            response=self.post('MV',self.day)
        links=response.context['conflict_links']
        self.assertIn(f'camp={existing.camp_id}',links['dispatcher_url'])
        self.assertIn('q=002',links['dispatcher_url'])
        self.assertEqual(links['history_url'],reverse('personnel:housing_history',args=[self.second_person.pk]))
        self.assertContains(response,'Открыть сотрудника в диспетчерской')
        self.assertContains(response,'Открыть историю размещений')
        self.assertContains(self.client.get(links['dispatcher_url']),self.second_employee.display_name)

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
        self.assertEqual(self.roster_row(timezone.localdate())['planned_departure_display'],arrival+timedelta(days=60))
        self.assertEqual(self.roster_row(timezone.localdate())['schedule_label'],'60/30')

    def test_rest_cycle_is_anchored_to_departure_without_fake_arrival(self):
        pattern=ShiftPattern.objects.create(company=self.company,board=self.board,name='60/30',on_days=60,off_days=30,cycle_codes=['10']*60+['МО']*30)
        departure=self.day-timedelta(days=10)
        self.assertEqual(self.post('MO',departure=departure,pattern=pattern.pk).status_code,302)
        row=self.roster_row(timezone.localdate());self.assertEqual(row['planned_return'],departure+timedelta(days=30));self.assertEqual(row['state_days'],12)
        self.assertFalse(CampStay.objects.filter(person=self.assigned().person).exists())
