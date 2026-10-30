from datetime import timedelta
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from . import test_camp_fuel as fixtures
from .camp_fuel_models import CampFuelDay

class CubicMetresTests(TestCase):
    setUpTestData=classmethod(fixtures.CampFuelTests.setUpTestData.__func__)
    def test_legacy_numbers_are_not_scaled_and_multiple_streams_merge_without_losing_sources(self):
        one=CampFuelDay.objects.create(company=self.company,camp=self.camp,date=self.day,fuel_type='DIESEL',opening_balance=10,received_liters=2,actual_liters=1,actual_balance=11)
        other=CampFuelDay.objects.create(company=self.company,camp=self.camp,date=self.day+timedelta(days=1),fuel_type='PETROL_92',opening_balance=3,received_liters=1,actual_liters=0,actual_balance=4)
        fn=import_module('apps.production.migrations.0031_camp_fuel_cubic_metres').combine_legacy
        fn(apps,SimpleNamespace(connection=connection));fn(apps,SimpleNamespace(connection=connection))
        current=list(CampFuelDay.objects.filter(fuel_type='TOTAL').order_by('date'));self.assertEqual(len(current),2)
        self.assertEqual(current[0].opening_balance,13);self.assertEqual(current[-1].actual_balance,15)
        one.refresh_from_db();self.assertEqual(one.opening_balance,10);self.assertEqual(other.opening_balance,3)
    def test_ui_single_balance_thousandths_and_no_fuel_selector(self):
        self.client.force_login(self.operator);url=reverse('production:camp_fuel');params={'camp':self.camp.pk,'date':str(self.day)}
        response=self.client.get(url,params);self.assertNotContains(response,'name="fuel_type"');self.assertContains(response,'м³')
        data=params|{'opening':'10.125','received':'0.500','consumed':'0.001','signed':response.context['form'].initial['signed']}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(CampFuelDay.objects.get(fuel_type='TOTAL').actual_balance,Decimal('10.624'))
