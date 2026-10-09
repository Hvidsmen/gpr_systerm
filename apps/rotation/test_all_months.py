from django.test import TestCase
from django.urls import reverse
from .tests import RotationTests
from .services import generate_people


class AllMonthsTests(TestCase):
    setUpTestData = classmethod(RotationTests.setUpTestData.__func__)

    def setUp(self):
        RotationTests.setUp(self)

    def test_all_period_has_month_groups_and_real_bounds(self):
        response=self.client.get(reverse('rotation:plan_detail',args=[self.plan.pk]),{'month':'all'})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.context['all_months'])
        self.assertEqual(response.context['days'][0],self.start)
        self.assertEqual(response.context['days'][-1],self.end)
        self.assertEqual([m['count'] for m in response.context['months']],[31,28,31,30,31,30])
        self.assertContains(response,'Все месяцы')
        self.assertNotContains(response,'Excel: выбранный месяц')
        self.assertContains(response,'Excel: весь период')

    def test_month_mode_and_actions_preserve_selected_mode(self):
        url=reverse('rotation:plan_detail',args=[self.plan.pk])
        response=self.client.get(url,{'month':'2026-02'})
        self.assertEqual(len(response.context['days']),28)
        self.assertFalse(response.context['all_months'])
        response=self.client.post(url,{'action':'refresh','month':'all'})
        self.assertIn('month=all',response.url)

    def test_status_edit_returns_to_all_months(self):
        generate_people(self.position)
        person=self.position.people.first()
        url=reverse('rotation:status_update',args=[person.pk])
        response=self.client.get(url,{'date':self.start.isoformat(),'view':'all'})
        self.assertContains(response,'name="view" value="all"')
        response=self.client.post(url,{'day':self.start.isoformat(),'status':'OFF','view':'all'})
        self.assertIn('month=all',response.url)
