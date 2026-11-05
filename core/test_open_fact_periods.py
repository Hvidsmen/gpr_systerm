from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company,Role,User
from core.fact_edit_window import can_edit_fact_day,window_message
from core.models import OpenFactPeriod


class OpenFactPeriodTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Периоды факта')
        self.other=Company.objects.create(name='Другая компания')
        self.admin_role=Role.objects.get(code='ADMIN')
        self.dispatcher_role=Role.objects.get(code='DISPATCHER')
        self.admin=User.objects.create_user(username='period-admin',company=self.company,role=self.admin_role)
        self.dispatcher=User.objects.create_user(username='period-dispatcher',company=self.company,role=self.dispatcher_role)
        self.day=timezone.localdate()-timedelta(days=20)

    def test_admin_page_adds_company_period_and_allows_old_fact_day(self):
        self.assertFalse(can_edit_fact_day(self.dispatcher,self.day))
        self.client.force_login(self.admin)
        response=self.client.post(reverse('accounts:fact_open_periods'),{'name':'Исправления октября','date_from':self.day,'date_to':self.day+timedelta(days=2)})
        self.assertRedirects(response,reverse('accounts:fact_open_periods'))
        period=OpenFactPeriod.objects.get();self.assertEqual(period.company,self.company)
        self.assertTrue(can_edit_fact_day(self.dispatcher,self.day))
        self.assertIn('Исправления октября',window_message(self.dispatcher,self.day))

    def test_period_is_company_scoped_future_stays_closed_and_delete_works(self):
        foreign=OpenFactPeriod.objects.create(company=self.other,date_from=self.day,date_to=self.day)
        self.assertFalse(can_edit_fact_day(self.dispatcher,self.day))
        OpenFactPeriod.objects.create(company=self.company,date_from=timezone.localdate()+timedelta(days=1),date_to=timezone.localdate()+timedelta(days=2))
        self.assertFalse(can_edit_fact_day(self.dispatcher,timezone.localdate()+timedelta(days=1)))
        self.client.force_login(self.admin)
        response=self.client.post(reverse('accounts:fact_open_periods'),{'action':'delete','period':foreign.pk})
        self.assertEqual(response.status_code,404)

    def test_non_admin_cannot_open_period_page(self):
        self.client.force_login(self.dispatcher)
        self.assertEqual(self.client.get(reverse('accounts:fact_open_periods')).status_code,403)
