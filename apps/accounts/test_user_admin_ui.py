from django.test import TestCase
from django.urls import reverse
from .test_user_filters import UserFilterTests
from .forms import CompanyUserAccessForm, CompanyUserCreateForm
from .models import User, Role
from apps.projects.models import Project, ConstructionObject


class UserAdminUITests(TestCase):
    setUp = UserFilterTests.setUp

    def test_ad_is_primary_and_local_form_shows_access_preview(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'Пользователи и доступ')
        self.assertContains(page, 'Добавить из AD')
        self.assertContains(page, 'Настроить доступ')
        page = self.client.get(reverse('accounts:user_create'))
        self.assertContains(page,'Доступ после сохранения')
        self.assertContains(page,'access-group-options')
        self.assertContains(page,'Новый локальный пользователь')
        page = self.client.get(reverse('accounts:user_access',args=[self.user.pk]))
        self.assertTrue(page.context['form'].fields['role'].disabled)
        self.assertTrue(page.context['form'].fields['assigned_objects'].disabled)

    def test_group_overrides_forged_rights_and_invalid_submission_preserves_input(self):
        obj = ConstructionObject.objects.create(company=self.company, project=Project.objects.create(company=self.company,name='P'),name='Один объект')
        self.group.assigned_objects.add(obj)
        payload={'username':'new-group-local','first_name':'Анна','last_name':'Иванова','email':'bad-email','access_group':self.group.pk,'role':Role.objects.get(code='ADMIN').pk,'is_active':'on','password1':'Useful-password-123!','password2':'Useful-password-123!'}
        page=self.client.post(reverse('accounts:user_create'),payload)
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Анна')
        self.assertEqual(page.context['form']['access_group'].value(),str(self.group.pk))
        self.assertFalse(User.objects.filter(username='new-group-local').exists())
        payload['email']='anna@example.org'
        self.assertEqual(self.client.post(reverse('accounts:user_create'),payload).status_code,302)
        user=User.objects.get(username='new-group-local')
        self.assertEqual(user.role,self.role)
        self.assertEqual(list(user.assigned_objects.all()),[obj])
        self.assertTrue(user.check_password(payload['password1']))
