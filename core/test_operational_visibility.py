from django.test import TestCase
from django.urls import reverse
from apps.production import test_transport as fixtures
from apps.production.transport import camps_for
from apps.personnel.lodging_forms import LodgingMovementForm
from apps.resources.models import Employee,Equipment
from apps.accounts.models import Company,User,Role

class OperationalVisibilityTests(TestCase):
    setUpTestData=classmethod(fixtures.TransportTests.setUpTestData.__func__)
    def test_mechanization_all_company_camps_and_equipment_without_plan_or_presence(self):
        self.operator.assigned_objects.clear();self.assertEqual(set(camps_for(self.operator).values_list('pk',flat=True)),{self.camp.pk,self.target.pk,self.unrelated.pk})
        self.client.force_login(self.operator)
        page=self.client.get(reverse('resources:equipment_unit_list'));self.assertContains(page,self.machine.inventory_number or self.machine.plate_number);self.assertEqual(self.client.get(reverse('resources:equipment_unit_detail',args=[self.machine.pk])).status_code,200)
        self.assertEqual(self.client.get(reverse('resources:equipment_unit_update',args=[self.machine.pk])).status_code,403)
    def test_commandant_selects_company_wide_and_personnel_catalogue_not_assigned_to_objects(self):
        employee=Employee.objects.create(company=self.company,last_name='Архивный',first_name='Сотрудник',is_active=False)
        foreign_company=Company.objects.create(name='Foreign');foreign=Employee.objects.create(company=foreign_company,last_name='Hidden',first_name='Employee')
        form=LodgingMovementForm(company=self.company,camp=self.camp,day=self.day)
        self.assertIn(employee,form.fields['employee'].queryset);self.assertNotIn(foreign,form.fields['employee'].queryset)
        user=self.users['PERSONNEL_DISPATCHER'];user.assigned_objects.clear();self.client.force_login(user)
        page=self.client.get(reverse('resources:employee_list'),{'include_archived':'1'});self.assertContains(page,'Архивный');self.assertNotContains(page,'Hidden')
