from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from . import test_access_groups as fixtures
from .models import Role,AccessGroup


class GroupUxTests(TestCase):
    setUp=fixtures.AccessGroupTests.setUp
    def test_filters_edit_dialog_member_links_and_top_level_personnel_link(self):
        other=AccessGroup.objects.create(company=self.company,name='Наблюдение',role=Role.objects.get(code='OBSERVER'))
        page=self.client.get(self.url)
        self.assertContains(page,'data-group-editor');self.assertContains(page,'data-open-group-editor')
        self.assertContains(page,'href="/personnel/housing/#roster"');self.assertContains(page,'Ручное назначение')
        self.assertEqual(len(self.client.get(self.url,{'ad':'linked'}).context['groups']),1)
        self.assertEqual([g.pk for g in self.client.get(self.url,{'q':'НАБЛЮДЕНИЕ'}).context['groups']],[other.pk])
        self.assertEqual([g.pk for g in self.client.get(self.url,{'role':other.role_id}).context['groups']],[other.pk])
        page=self.client.get(reverse('accounts:access_group_edit',args=[self.group.pk]))
        self.assertTrue(page.context['editor_open']);self.assertContains(page,'Изменить группу')
    def test_commandant_role_is_selectable_in_group_and_has_existing_lodging_rights(self):
        from .forms import CompanyUserAccessForm
        from apps.personnel.models import Camp
        role=Role.objects.get(code='COMMANDANT');self.assertEqual(role.name,'Комендант участка')
        self.assertNotIn('ВЖГ', role.description)
        assigned=Camp.objects.create(company=self.company,name='Дудинка')
        forbidden=Camp.objects.create(company=self.company,name='Чужое местонахождение')
        page=self.client.get(self.url);self.assertIn(role,page.context['form'].fields['role'].queryset)
        self.assertContains(page,'Местонахождения коменданта')
        form=CompanyUserAccessForm(company=self.company,instance=self.user);self.assertIn(role,form.fields['role'].queryset)
        invalid=self.client.post(reverse('accounts:access_group_edit',args=[self.group.pk]),{
            'name':self.group.name,'role':role.pk,'members':[self.user.pk]})
        self.assertEqual(invalid.status_code,200)
        self.assertContains(invalid,'Выберите хотя бы одно местонахождение для коменданта.')
        self.client.post(reverse('accounts:access_group_edit',args=[self.group.pk]),{
            'name':self.group.name,'role':role.pk,'members':[self.user.pk],'assigned_camps':[assigned.pk]})
        self.user.refresh_from_db();self.assertEqual(self.user.role,role)
        self.assertEqual(list(self.group.assigned_camps.all()),[assigned])
        self.client.force_login(self.user)
        lodging=self.client.get(reverse('personnel:lodging'))
        self.assertEqual(lodging.status_code,200);self.assertContains(lodging,assigned.name)
        self.assertNotContains(lodging,forbidden.name)
        self.assertEqual(self.client.get(reverse('personnel:lodging'),{'camp':forbidden.pk}).status_code,404)
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_role_descriptions_use_location_terminology(self):
        for code in ('COMMANDANT', 'FUEL_WAREHOUSE'):
            with self.subTest(code=code):
                self.assertNotIn('ВЖГ', Role.objects.get(code=code).description)
