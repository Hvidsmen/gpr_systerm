from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from .test_housing import HousingTests
from .housing_views import roster_rows
from . import housing
from .models import HousingEvent


class HousingImprovementsTests(TestCase):
    @classmethod
    def setUpTestData(cls):HousingTests.setUpTestData.__func__(cls)
    def setUp(self):self.client.force_login(self.hr)
    def test_linked_card_number_and_redirect_use_employee_catalogue(self):
        self.person.employee_number='';self.person.save()
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day)})
        self.assertContains(response,self.employee.employee_number)
        self.assertContains(response,reverse('resources:employee_detail',args=[self.employee.pk]))
        self.assertRedirects(self.client.get(reverse('personnel:person_edit',args=[self.person.pk])),reverse('resources:employee_detail',args=[self.employee.pk]),fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('resources:employee_detail',args=[self.employee.pk])).status_code,200)
    def test_departure_destination_transfers_or_records_rest(self):
        data={'camp':self.a.pk,'date':str(self.day),'kind':'DEPARTURE','employees':[self.employee.pk],'destination':'CAMP','target':self.b.pk,'snapshot':housing.movement_snapshot(self.hr)}
        self.assertEqual(self.client.post(reverse('personnel:housing'),data).status_code,302)
        event=HousingEvent.objects.get();self.assertEqual(event.kind,'TRANSFER');self.assertEqual(event.departure_destination,'CAMP');self.assertEqual(event.to_camp,self.b)
        data.update(camp=self.b.pk,destination='REST',target='',snapshot=housing.movement_snapshot(self.hr))
        self.assertEqual(self.client.post(reverse('personnel:housing'),data).status_code,302)
        event=HousingEvent.objects.order_by('-pk').first();self.assertEqual(event.kind,'DEPARTURE');self.assertEqual(event.departure_destination,'REST')
    def test_warning_starts_fourteen_days_before_and_overdue_does_not_disappear(self):
        self.pattern.on_days=15;self.pattern.off_days=30;self.pattern.cycle_codes=['10']*15+['ДП']+['МО']*29
        self.pattern.calendar={str(self.day+timedelta(days=14)):'ДП',str(self.day+timedelta(days=15)):'МО'};self.pattern.save()
        rows,_=roster_rows(self.company,self.a,self.day);self.assertEqual(rows[0]['departure_warning'],'soon')
        rows,_=roster_rows(self.company,self.a,self.day-timedelta(days=1));self.assertEqual(rows[0]['departure_warning'],'')
        rows,_=roster_rows(self.company,self.a,self.day+timedelta(days=16));self.assertEqual(rows[0]['departure_warning'],'overdue')
        self.assertEqual(rows[0]['planned_departure'],str(self.day+timedelta(days=14)))
    def test_calendar_shows_individual_overrides_and_rejects_other_company(self):
        self.slot.calendar={str(self.day):'В'};self.slot.save()
        url=reverse('personnel:individual_calendar',args=[self.person.pk]);response=self.client.get(url,{'month':self.day.strftime('%Y-%m')})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['days'][self.day.day-1]['code'],'В')
        self.assertEqual(response.json()['number'],self.employee.employee_number)
        self.assertEqual(self.client.get(reverse('personnel:individual_calendar',args=[self.foreign_person.pk])).status_code,404)
        self.assertEqual(self.client.get(url,{'month':'wrong'}).status_code,400)

    def reconciliation_source(self):
        from .models import StaffingSlot
        slot=StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,housing_camp=self.a,status='МВ',arrival_date=self.day)
        url=reverse('personnel:housing_reconcile')
        response=self.client.get(url,{'camp':self.a.pk,'date':str(self.day)})
        self.assertEqual(response.status_code,200)
        data={'camp':self.a.pk,'date':str(self.day),'person':[self.second_person.pk],
              'snapshot':response.context['snapshot'],'source_snapshot':response.context['source_snapshot']}
        return slot,url,data

    def test_reconciliation_transfers_verified_source_and_keeps_original_date(self):
        slot,url,data=self.reconciliation_source()
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        stay=self.second_person.stays.get()
        self.assertEqual(stay.arrived_on,slot.arrival_date)
        self.assertEqual(stay.camp,self.a)
        self.assertTrue(HousingEvent.objects.filter(person=self.second_person,recorded_by=self.hr).exists())

    def test_reconciliation_rejects_source_changed_after_review(self):
        slot,url,data=self.reconciliation_source()
        slot.arrival_date-=timedelta(days=1);slot.save()
        response=self.client.post(url,data)
        self.assertContains(response,'Кадровые таблицы изменились')
        self.assertFalse(self.second_person.stays.exists())

    def test_position_filter_contains_only_scoped_rows_and_arrival_link_selects_employee(self):
        from apps.resources.models import Position
        absent=Position.objects.create(company=self.company,code='ABSENT',name='Нет в выбранном составе')
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day)})
        positions=response.context['rotation_filter'].fields['position'].queryset
        self.assertNotIn(absent,positions)
        if self.employee.position_id:self.assertIn(self.employee.position,positions)
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day),'position':absent.pk})
        self.assertIsNone(response.context['rotation_filter'].cleaned_data['position'])
        people=self.client.get(reverse('personnel:people'));self.assertContains(people,'>Заезд</a>')
        arrival=self.client.get(reverse('personnel:housing'),{'action':'ARRIVAL','employee':self.employee.pk,'choose_camp':'1'})
        self.assertEqual(arrival.status_code,200);self.assertEqual(arrival.context['form'].initial['employees'],[self.employee.pk]);self.assertIsNone(arrival.context['form'].initial['camp'])

    def test_schedule_label_comes_from_employee_card(self):
        self.employee.zup_card={'schedule':'Вахта 1.2М (60х30)'};self.employee.save()
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day)})
        row=next(r for r in response.context['rows'] if r.get('person') and r['person'].employee_id==self.employee.pk)
        self.assertEqual(row['schedule_label'],'Вахта 1.2М (60х30)')
        self.assertEqual(response.context['calendar_end'].year,self.day.year+1)
        self.assertGreater(len(response.context['calendar_dates']),365)

    def test_status_indicator_is_critical_before_presence(self):
        from .rotation import status_indicator
        self.assertEqual(status_indicator({'state':'MV','departure_warning':'overdue'})[:2],('red','Выезд просрочен'))
        self.assertEqual(status_indicator({'state':'MV'})[:2],('green','На вахте'))
        self.assertEqual(status_indicator({'state':'MO','arrival_overdue':True})[:2],('red','Заезд просрочен'))


import json,os,subprocess,shutil
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase


@skipUnless(os.environ.get('ROTATION_FILTER_BROWSER_TESTS')=='1','Optional calendar scroll browser scenario')
class RotationCalendarBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_selected_day_centres_and_top_scroll_syncs(self):
        HousingTests.setUpTestData.__func__(type(self));self.client.force_login(self.hr)
        config={'base':self.live_server_url,'path':reverse('personnel:housing')+f'?camp={self.a.pk}&date={self.day}#calendar','cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium')}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/rotation_calendar.js')],capture_output=True,text=True,env={**os.environ,'ROTATION_CALENDAR_CONFIG':json.dumps(config)},timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())

class MovementScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):HousingTests.setUpTestData.__func__(cls)
    def setUp(self):self.client.force_login(self.hr)
    def test_bulk_form_keeps_multiple_employees_and_personal_form_ignores_tampered_selection(self):
        from .forms import MovementForm
        common={'kind':'DEPARTURE','date':str(self.day),'camp':self.a.pk,'employees':[self.employee.pk,self.second_employee.pk],'destination':'REST','snapshot':housing.movement_snapshot(self.hr)}
        housing.move(self.hr,'ARRIVAL',self.a,self.day,[self.second_person])
        common['snapshot']=housing.movement_snapshot(self.hr)
        form=MovementForm(common,company=self.company,camp=self.a,day=self.day)
        self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['employees'].count(),2)
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day),'employee':self.employee.pk,'action':'DEPARTURE'})
        token=response.context['form'].initial['personal_scope']
        response=self.client.post(reverse('personnel:housing'),{**common,'personal_scope':token})
        self.assertEqual(response.status_code,302)
        self.stay.refresh_from_db();self.assertEqual(self.stay.departed_on,self.day)
        self.assertIsNone(self.second_person.stays.get().departed_on)
    def test_initial_departure_supports_backdate_without_inventing_arrival(self):
        from .models import CampStay,StaffingSlot
        StaffingSlot.objects.create(company=self.company,person=self.second_person,group=self.group,pattern=self.pattern,housing_camp=self.a,status='МО')
        url=reverse('personnel:initial_departure',args=[self.second_person.pk]);past=self.day-timedelta(days=100)
        data={'date':str(past),'camp':self.a.pk,'comment':'Уточнение по ведомости','version':''}
        self.assertEqual(self.client.post(url,data).status_code,302)
        event=HousingEvent.objects.get(person=self.second_person);self.assertEqual(event.date,past);self.assertIsNone(event.stay_id)
        self.assertFalse(CampStay.objects.filter(person=self.second_person).exists())
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day)})
        row=next(row for row in response.context['rows'] if row.get('person')==self.second_person)
        self.assertEqual(row['state'],'MO');self.assertEqual(row['state_days'],101)
        self.assertContains(self.client.post(url,data),'другой пользователь')
        self.assertEqual(self.client.get(reverse('personnel:initial_departure',args=[self.foreign_person.pk])).status_code,404)
    def test_initial_departure_rejects_existing_actual_stay_and_future_date(self):
        url=reverse('personnel:initial_departure',args=[self.person.pk])
        data={'date':str(self.day),'camp':self.a.pk,'comment':'Уточнение','version':''}
        self.assertContains(self.client.post(url,data),'История размещения уже оформлена')
        data['date']=str(self.day+timedelta(days=10))
        self.assertContains(self.client.post(url,data),'будущей датой')
        self.assertFalse(HousingEvent.objects.exists())
