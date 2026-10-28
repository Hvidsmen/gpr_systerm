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
