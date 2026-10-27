from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company, Role, User
from apps.resources.models import Employee
from .forms import PersonForm
from .models import Person, Camp, CampStay
from . import housing


class ActiveEmployeeLinkTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Personnel links')
        self.user=User.objects.create_user(username='hr-active-links',company=self.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        self.active=Employee.objects.create(company=self.company,last_name='Шмыков',first_name='Наиль',middle_name='Жавдатович',employee_number='09943')
        self.inactive=Employee.objects.create(company=self.company,last_name='Шмыков',first_name='Наиль',middle_name='Жавдатович',employee_number='04094',is_active=False)
        self.person=Person.objects.create(company=self.company,full_name=self.active.display_name)
        self.client.force_login(self.user)

    def test_new_link_offers_only_active_same_company_employees_with_numbers(self):
        foreign=Employee.objects.create(company=Company.objects.create(name='Other'),last_name='Foreign',is_active=True)
        form=PersonForm(instance=self.person)
        self.assertEqual(list(form.fields['employee'].queryset),[self.active])
        response=self.client.get(reverse('personnel:person_edit',args=[self.person.pk]))
        self.assertContains(response,'09943');self.assertNotContains(response,'04094');self.assertNotContains(response,'Foreign')
        self.assertNotIn(foreign,form.fields['employee'].queryset)

    def test_post_cannot_create_archived_link_and_active_link_saves(self):
        url=reverse('personnel:person_edit',args=[self.person.pk])
        response=self.client.post(url,{'full_name':self.person.full_name,'employee':self.inactive.pk})
        self.assertEqual(response.status_code,200);self.assertIn('employee',response.context['form'].errors)
        self.person.refresh_from_db();self.assertIsNone(self.person.employee_id)
        response=self.client.post(url,{'full_name':self.person.full_name,'employee':self.active.pk})
        self.assertEqual(response.status_code,302)
        self.person.refresh_from_db();self.assertEqual(self.person.employee_id,self.active.pk)

    def test_auto_link_ignores_inactive_namesake_and_does_not_link_archive_only(self):
        self.assertEqual(housing.matching_employee(self.person),self.active)
        self.assertEqual(housing.link_people(self.company),1)
        self.person.refresh_from_db();self.assertEqual(self.person.employee_id,self.active.pk)
        self.active.is_active=False;self.active.save(update_fields=['is_active'])
        self.person.employee=None;self.person.save(update_fields=['employee'])
        self.assertIsNone(housing.matching_employee(self.person))
        self.assertEqual(housing.link_people(self.company),0)

    def test_existing_inactive_link_and_housing_history_are_preserved(self):
        self.person.employee=self.inactive;self.person.save(update_fields=['employee'])
        camp=Camp.objects.create(company=self.company,name='ВЖГ')
        stay=CampStay.objects.create(company=self.company,camp=camp,person=self.person,arrived_on=timezone.localdate())
        form=PersonForm({'full_name':self.person.full_name,'employee':self.inactive.pk,'phone':'123'},instance=self.person)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        self.person.refresh_from_db();self.assertEqual(self.person.employee_id,self.inactive.pk)
        self.assertTrue(self.person.stays.filter(pk=stay.pk).exists())
        other=Employee.objects.create(company=self.company,last_name='Другой архив',is_active=False)
        self.assertNotIn(other,PersonForm(instance=self.person).fields['employee'].queryset)
