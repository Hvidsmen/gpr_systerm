from datetime import timedelta
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User, Role
from apps.personnel.models import Camp, CampObjectLink
from apps.planning.models import WorkTask
from apps.planning.task_services import generate_daily
from .test_dispatch import DispatcherTests
from .models import FuelFact, FactSectionDay, EquipmentActivity
from . import camp_fuel
from .camp_fuel_models import CampFuelDay
from .history_views import visible_revisions
from .history_models import FactRevision


class FuelWarehouseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        DispatcherTests.setUpTestData.__func__(cls)
        user = User.objects.create_user(username='fuel-warehouse', company=cls.company, role=Role.objects.get(code='FUEL_WAREHOUSE'))
        user.assigned_objects.add(cls.obj)
        cls.users['FUEL_WAREHOUSE'] = user
        cls.fuel_user = user
    payload = DispatcherTests.payload

    def test_object_fuel_is_removed_and_old_bookmarks_open_camp_ledger(self):
        self.client.force_login(self.fuel_user)
        self.assertRedirects(self.client.get(self.url),reverse('production:camp_fuel'),fetch_redirect_response=False)
        self.assertEqual(self.client.post(self.url,{'section':'fuel'}).status_code,403)
        self.assertEqual(generate_daily(self.fuel_user),0)
        self.assertFalse(WorkTask.objects.filter(assigned_to=self.fuel_user,kind='FACT').exists())
        page=self.client.get(reverse('production:camp_fuel'))
        self.assertNotContains(page,'ГСМ по объекту');self.assertNotContains(page,'Назначения и состояния техники')
        self.client.force_login(self.users['ADMIN'])
        self.assertRedirects(self.client.get(self.url,{'section':'fuel'}),reverse('production:camp_fuel'),fetch_redirect_response=False)
        self.assertEqual(self.client.post(self.url,{'section':'fuel'}).status_code,403)
        self.assertFalse(FuelFact.objects.exists())

    def test_camp_balance_and_export_without_transport_access(self):
        self.client.force_login(self.fuel_user)
        params = {'camp':self.housing_camp.pk,'date':str(self.day),'fuel_type':'TOTAL'}
        url = reverse('production:camp_fuel')
        page = self.client.get(url, params)
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Сохранить баланс')
        data = params | {'opening':'100','received':'20','consumed':'10','signed':page.context['form'].initial['signed']}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(CampFuelDay.objects.get().actual_balance,110)
        self.assertEqual(self.client.get(url, params | {'export':'xlsx'}).status_code,200)
        for route in ['production:transport_board','accounts:user_list','resources:equipment_type_list','planning:workspace_list']:
            self.assertEqual(self.client.get(reverse(route)).status_code,403)
        hidden = Camp.objects.create(company=self.company,name='Другой ВЖГ')
        CampObjectLink.objects.create(company=self.company,camp=hidden,construction_object=self.other)
        self.assertEqual(self.client.post(url, data | {'camp':hidden.pk}).status_code,400)
        self.fuel_user.assigned_objects.clear()
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.assertEqual(CampFuelDay.objects.get().actual_balance,110)

    def test_service_scope_closed_day_and_history_are_enforced(self):
        with self.assertRaises(ValidationError):
            camp_fuel.save_day(self.fuel_user,self.housing_camp,self.day-timedelta(days=10),'TOTAL',opening=100,received=0,consumed=0,signed=camp_fuel.token(self.fuel_user,self.housing_camp,self.day-timedelta(days=10),'TOTAL'))
        camp_fuel.save_day(self.fuel_user,self.housing_camp,self.day,'TOTAL',opening=100,received=0,consumed=0,signed=camp_fuel.token(self.fuel_user,self.housing_camp,self.day,'TOTAL'))
        self.assertEqual(set(visible_revisions(self.fuel_user).values_list('entity_type',flat=True)),{'production.campfuelday'})
        self.fuel_user.assigned_objects.clear()
        self.assertFalse(visible_revisions(self.fuel_user).exists())
        with self.assertRaises(PermissionDenied):
            camp_fuel.save_day(self.fuel_user,self.housing_camp,self.day,'TOTAL',opening=100,received=0,consumed=0,signed='forged')

    def test_mechanics_form_has_no_fuel_and_stale_combined_form_is_rejected(self):
        data = self.payload('MECHANIZATION_DISPATCHER','mechanization')
        self.assertFalse(any(key.startswith('fuel-') for key in data))
        self.assertEqual(self.client.post(self.url,data | {'fuel-TOTAL_FORMS':'1'}).status_code,403)
        self.assertFalse(FuelFact.objects.exists())

    def test_migration_preserves_old_combined_confirmations_idempotently(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        old=FactSectionDay.objects.create(company=self.company,construction_object=self.obj,date=self.day,section='mechanization',revision=7,confirmed_at=timezone.now(),confirmed_by=self.users['ADMIN'],deviation_comment='Проверено')
        migrate=import_module('apps.production.migrations.0029_separate_fuel_direction').preserve_confirmations
        migrate(apps,SimpleNamespace(connection=connection))
        migrate(apps,SimpleNamespace(connection=connection))
        fuel=FactSectionDay.objects.get(section='fuel')
        self.assertEqual(fuel.revision,old.revision)
        self.assertEqual(fuel.confirmed_at,old.confirmed_at)
        self.assertEqual(fuel.confirmed_by,old.confirmed_by)
        self.assertEqual(fuel.deviation_comment,old.deviation_comment)
