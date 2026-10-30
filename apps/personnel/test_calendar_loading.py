from django.test import TestCase
from django.urls import reverse
from . import test_housing as fixtures
from .models import Person,StaffingSlot

class CalendarLoadingTests(TestCase):
    setUpTestData=classmethod(fixtures.HousingTests.setUpTestData.__func__)
    def test_initial_page_has_no_hidden_grid_and_calendar_is_bounded_and_person_jump_is_preserved(self):
        people=Person.objects.bulk_create([Person(company=self.company,full_name='Работник '+str(n),employee_number='CAL'+str(n)) for n in range(65)])
        StaffingSlot.objects.bulk_create([StaffingSlot(company=self.company,group=self.group,person=p,housing_camp=self.a,status='МВ',pattern=self.pattern,arrival_date=self.day) for p in people])
        self.client.force_login(self.hr);url=reverse('personnel:housing');params={'camp':self.a.pk,'date':str(self.day)}
        page=self.client.get(url,params);self.assertEqual(page.status_code,200);self.assertNotContains(page,'data-calendar-table-scroll');self.assertContains(page,'data-rotation-calendar-host')
        fragment=self.client.get(url,params|{'calendar_fragment':'1'})
        self.assertEqual(len(fragment.context['rows']),50);self.assertEqual(fragment.context['calendar_end'].year,self.day.year+1);self.assertNotContains(fragment,'<html')
        last=page.context['rows'][-1]['person']
        if last:
            fragment=self.client.get(url,params|{'calendar_fragment':'1','calendar_person':last.pk})
            self.assertIn(last.pk,[r['person'].pk for r in fragment.context['rows'] if r.get('person')])
        self.assertLessEqual(len(fragment.context['rows']),50)
