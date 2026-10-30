from django.test import TestCase, Client
from django.urls import reverse
from apps.accounts.models import User, Role
from . import test_catalogs as fixtures
from .models import MeasurementUnit, ProjectWork


class UnitActionTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkCatalogTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.user)
        self.unit = MeasurementUnit.objects.create(
            company=self.company, symbol="рейс.", name="Рейс"
        )
        self.edit = reverse("works:unit_update", args=[self.unit.pk])
        self.delete = reverse("works:unit_delete", args=[self.unit.pk])

    def test_buttons_and_edit(self):
        response = self.client.get(reverse("works:unit_list"))
        self.assertContains(response, self.edit)
        self.assertContains(response, self.delete)
        self.assertEqual(self.client.get(self.edit).status_code, 200)
        self.assertRedirects(
            self.client.post(self.edit, {"symbol": "рейс", "name": "Рейсы"}),
            reverse("works:unit_list"),
        )
        self.unit.refresh_from_db()
        self.assertEqual((self.unit.symbol, self.unit.name), ("рейс", "Рейсы"))
        duplicate = self.client.post(self.edit, {"symbol": "м", "name": "Метр"})
        self.assertTrue(duplicate.context["form"].errors)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.symbol, "рейс")

    def test_delete_requires_post_and_csrf(self):
        self.assertEqual(self.client.get(self.delete).status_code, 200)
        self.assertTrue(MeasurementUnit.objects.filter(pk=self.unit.pk).exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.delete).status_code, 403)
        self.assertTrue(MeasurementUnit.objects.filter(pk=self.unit.pk).exists())
        self.assertRedirects(self.client.post(self.delete), reverse("works:unit_list"))
        self.assertFalse(MeasurementUnit.objects.filter(pk=self.unit.pk).exists())

    def test_used_unit_requires_rename_preview_and_name_can_change_directly(self):
        work = ProjectWork.objects.create(
            company=self.company,
            section=self.section,
            name="Work",
            unit=self.unit.symbol,
            load_profile=self.profile,
        )
        self.assertEqual(self.client.post(self.delete).status_code, 400)
        response = self.client.post(self.edit, {"symbol": "рейс", "name": "Рейс"})
        self.assertContains(response,"data-unit-rename-preview")
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.symbol, "рейс.")
        self.assertRedirects(
            self.client.post(self.edit, {"symbol": "рейс.", "name": "Рейсы"}),
            reverse("works:unit_list"),
        )
        work.refresh_from_db()
        self.assertEqual(work.unit, "рейс.")

    def test_company_scope_for_get_and_post(self):
        unit = MeasurementUnit.objects.create(
            company=self.foreign, symbol="private", name="Private"
        )
        for route in ["unit_update", "unit_delete"]:
            url = reverse("works:" + route, args=[unit.pk])
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(
                self.client.post(url, {"symbol": "x", "name": "X"}).status_code, 404
            )
        self.assertTrue(MeasurementUnit.objects.filter(pk=unit.pk).exists())

    def test_readonly_role_cannot_change_catalog(self):
        user = User.objects.create_user(
            username="unit-reader",
            company=self.company,
            role=Role.objects.get(code="MANAGER"),
        )
        self.client.force_login(user)
        response = self.client.get(reverse("works:unit_list"))
        self.assertNotContains(response, self.edit)
        self.assertNotContains(response, self.delete)
        for url in [self.edit, self.delete]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(
                self.client.post(url, {"symbol": "x", "name": "X"}).status_code, 403
            )

    def test_self_alias_does_not_block_name_edit_and_confirmed_rename_updates_refs(self):
        from .models import MeasurementUnitAlias,ProjectWorkItem
        work=ProjectWork.objects.create(company=self.company,section=self.section,name='Комплекс',unit=self.unit.symbol,kind='COMPOSITE')
        item=ProjectWorkItem.objects.create(company=self.company,project_work=work,name='Состав',unit=self.unit.symbol,quantity_per_unit=3,weight=100)
        MeasurementUnitAlias.objects.create(company=self.company,symbol=self.unit.symbol,target=self.unit)
        self.assertEqual(self.client.post(self.edit,{'symbol':self.unit.symbol,'name':'Название исправлено'}).status_code,302)
        preview=self.client.post(self.edit,{'symbol':'рейс','name':'Рейс'})
        self.assertContains(preview,'Подтвердить изменение');token=preview.context['preview']['token']
        self.unit.refresh_from_db();self.assertEqual(self.unit.symbol,'рейс.')
        self.assertEqual(self.client.post(self.edit,{'action':'confirm_rename','token':token}).status_code,400)
        self.assertEqual(self.client.post(self.edit,{'action':'confirm_rename','token':token,'equivalent':'on'}).status_code,302)
        work.refresh_from_db();item.refresh_from_db();self.unit.refresh_from_db()
        self.assertEqual((work.unit,item.unit,self.unit.symbol),('рейс','рейс','рейс'));self.assertEqual(item.quantity_per_unit,3)
        self.assertEqual(MeasurementUnitAlias.objects.get(company=self.company,symbol='рейс.').target,self.unit)
        self.assertFalse(MeasurementUnitAlias.objects.filter(company=self.company,symbol=self.unit.symbol).exists())

    def test_rename_stale_usage_or_tampered_token_is_nonmutating(self):
        ProjectWork.objects.create(company=self.company,section=self.section,name='Работа',unit=self.unit.symbol,load_profile=self.profile)
        preview=self.client.post(self.edit,{'symbol':'рейс','name':'Рейс'})
        token=preview.context['preview']['token']
        ProjectWork.objects.create(company=self.company,section=self.section,name='Поздняя работа',unit=self.unit.symbol,load_profile=self.profile)
        for value in [token,'tampered']:
            self.assertEqual(self.client.post(self.edit,{'action':'confirm_rename','token':value,'equivalent':'on'}).status_code,400)
        self.unit.refresh_from_db();self.assertEqual(self.unit.symbol,'рейс.')
