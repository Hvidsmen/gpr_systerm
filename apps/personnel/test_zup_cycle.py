"""The displayed schedule, roster forecast and popup must use the same cycle."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from . import test_housing as fixtures
from .models import CampStay, HousingEvent, Person, ShiftPattern, StaffingSlot
from .rotation import rotation_rows
from .rotation_calendar import cycle_definition, named_cycle
from .services import calendar_for


class NamedCycleTests(SimpleTestCase):
    def test_supported_spellings_and_explicit_fields_have_priority(self):
        for name in ('Вахта 1.1М (60х30)','60Х30','60x30','60X30','60×30','60/30','60\\30','60 * 30'):
            with self.subTest(name=name):
                self.assertEqual(named_cycle(name),(60,30))
                pattern=SimpleNamespace(updated_at=None,name=name,on_days=0,off_days=0,calendar={},cycle_codes=[])
                self.assertEqual(cycle_definition(pattern)['codes'],['МВ']*60+['МО']*30)
                pattern.on_days=14;pattern.off_days=7
                self.assertEqual(cycle_definition(pattern)['on_days'],14)

    def test_ambiguous_invalid_and_fractional_lengths_are_not_guessed(self):
        for name in ('60х30 или 45х45','0/30','366/30','60/0','60.5х30','60х30,5','График без цикла'):
            with self.subTest(name=name):self.assertIsNone(named_cycle(name))


class ZupCycleTests(TestCase):
    setUpTestData=classmethod(fixtures.HousingTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.hr)
        self.employee.zup_card={'schedule':'Вахта 1.1М (60х30)'};self.employee.save()
        self.slot.pattern=None;self.slot.save()
        self.person.schedule_slot=self.slot;self.person.save()
        self.stay.arrived_on=self.day-timedelta(days=9);self.stay.save()

    def row(self):
        return next(r for r in rotation_rows(self.company,[self.a],self.day)[0] if r['person'].pk==self.person.pk)

    def test_hr_cycle_applies_to_existing_assignment_without_resaving_it(self):
        row=self.row()
        self.assertEqual(row['schedule_label'],'Вахта 1.1М (60х30)')
        self.assertEqual(row['schedule_cycle_label'],'60/30');self.assertTrue(row['schedule_from_zup'])
        self.assertFalse(row['cycle_missing']);self.assertEqual(row['today_code'],'МВ')
        self.assertEqual(row['planned_departure_display'],self.stay.arrived_on+timedelta(days=60))
        self.assertEqual(row['state_days'],10)
        self.slot.refresh_from_db();self.assertIsNone(self.slot.pattern_id)

    def test_popup_calendar_grid_and_roster_share_hr_source(self):
        row=self.row()
        popup=self.client.get(reverse('personnel:individual_calendar',args=[self.person.pk]),{'month':self.day.strftime('%Y-%m')}).json()
        self.assertEqual(popup['pattern'],row['schedule_label']);self.assertIn('60/30 · из ЗУП',popup['schedule_info'])
        self.assertEqual(popup['days'][self.day.day-1]['code'],row['today_code'])
        page=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day,'calendar_fragment':'1'})
        projected=next(r for r in page.context['rows'] if r['person'].pk==self.person.pk)
        index=page.context['calendar_dates'].index(self.day)
        self.assertEqual(projected['calendar_codes'][index],row['today_code'])

    def test_actual_mo_reanchors_thirty_day_rest(self):
        self.stay.departed_on=self.day-timedelta(days=3);self.stay.save()
        row=self.row();self.assertEqual(row['state'],'MO')
        self.assertEqual(row['today_code'],'МО')
        self.assertEqual(row['planned_return'],self.stay.departed_on+timedelta(days=30))

    def test_initial_mo_without_invented_arrival_uses_hr_cycle(self):
        self.stay.cancelled_at=timezone.now();self.stay.save()
        HousingEvent.objects.create(company=self.company,person=self.person,kind='DEPARTURE',from_camp=self.a,date=self.day-timedelta(days=5),departure_destination='REST')
        row=self.row();self.assertEqual(row['state'],'MO');self.assertEqual(row['today_code'],'МО')
        self.assertEqual(row['planned_return'],self.day+timedelta(days=25))

    def test_hr_cycle_also_works_for_actual_employee_without_staffing_slot(self):
        self.slot.delete()
        row=self.row();self.assertIsNone(row['slot']);self.assertEqual(row['schedule_cycle_label'],'60/30')
        self.assertEqual(row['today_code'],'МВ')
        popup=self.client.get(reverse('personnel:individual_calendar',args=[self.person.pk]),{'month':self.day.strftime('%Y-%m')}).json()
        self.assertEqual(popup['days'][self.day.day-1]['code'],'МВ')

    def test_explicit_personal_cycle_and_label_override_hr(self):
        pattern=ShiftPattern.objects.create(company=self.company,board=self.board,name='Индивидуальный (14/7)',on_days=14,off_days=7,cycle_codes=['8']*14+['МО']*7)
        self.slot.pattern=pattern;self.slot.save()
        row=self.row();self.assertEqual(row['schedule_label'],str(pattern));self.assertEqual(row['schedule_cycle_label'],'14/7')
        self.assertFalse(row['schedule_from_zup']);self.assertEqual(row['today_code'],'8')
        self.assertEqual(row['planned_departure_display'],self.stay.arrived_on+timedelta(days=14))
        popup=self.client.get(reverse('personnel:individual_calendar',args=[self.person.pk]),{'month':self.day.strftime('%Y-%m')}).json()
        self.assertEqual(popup['pattern'],str(pattern));self.assertEqual(popup['days'][self.day.day-1]['code'],'8')

    def test_invalid_explicit_cycle_does_not_silently_fall_back_to_hr(self):
        pattern=ShiftPattern.objects.create(company=self.company,board=self.board,name='Особый график')
        self.slot.pattern=pattern;self.slot.save()
        row=self.row();self.assertTrue(row['cycle_missing']);self.assertEqual(row['schedule_label'],str(pattern))
        self.assertFalse(row['schedule_from_zup']);self.assertIsNone(row['planned_departure_display'])

    def test_weekly_and_ambiguous_hr_schedules_do_not_become_rotations(self):
        for name in ('Пятидневка 5/2','Вахта 60х30 или 45х45'):
            self.employee.zup_card={'schedule':name};self.employee.save()
            row=self.row();self.assertTrue(row['cycle_missing']);self.assertEqual(row['today_code'],'')
            self.assertIsNone(row['planned_departure_display'])

    def test_forecast_is_read_only_preserves_manual_exceptions_and_does_not_invent_hours(self):
        self.slot.calendar={str(self.day):'В',str(self.day+timedelta(days=1)):'8'};self.slot.save()
        before=deepcopy((list(ShiftPattern.objects.values()),list(StaffingSlot.objects.values()),list(CampStay.objects.values()),list(HousingEvent.objects.values())))
        values=calendar_for(self.slot,start=self.day,end=self.day+timedelta(days=90))
        self.assertEqual(values[str(self.day)],'В');self.assertEqual(values[str(self.day+timedelta(days=1))],'8')
        self.assertEqual(values[str(self.day+timedelta(days=2))],'МВ')
        self.assertEqual((list(ShiftPattern.objects.values()),list(StaffingSlot.objects.values()),list(CampStay.objects.values()),list(HousingEvent.objects.values())),before)

    def test_no_actual_or_initial_anchor_leaves_dates_unknown(self):
        self.stay.cancelled_at=timezone.now();self.stay.save()
        row=self.row();self.assertFalse(row['cycle_missing']);self.assertEqual(row['today_code'],'')
        self.assertIsNone(row['planned_departure_display']);self.assertIsNone(row['planned_return'])

    def test_plan_assignment_does_not_hide_separately_selected_personal_schedule(self):
        from apps.resources.models import Brigade
        role=Brigade.objects.create(company=self.company,name='Начальник участка')
        self.client.post(reverse('personnel:plan_assignment_create'),{'employee':self.employee.pk,'planning_position':role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(self.day)})
        assignment=StaffingSlot.objects.get(planning_position=role)
        pattern=ShiftPattern.objects.create(company=self.company,board=self.board,name='14/7',on_days=14,off_days=7,cycle_codes=['8']*14+['МО']*7)
        self.slot.pattern=pattern;self.slot.save();self.person.schedule_slot=self.slot;self.person.save()
        row=self.row();self.assertEqual(row['slot'].pk,assignment.pk);self.assertEqual(row['position_label'],role.name)
        self.assertEqual(row['schedule_slot'].pk,self.slot.pk);self.assertEqual(row['today_code'],'8')
        self.assertEqual(row['schedule_label'],str(pattern));self.assertEqual(row['schedule_cycle_label'],'14/7')
