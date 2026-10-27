from django.test import TestCase
from django.urls import reverse
from .test_housing import HousingTests
from .models import Person, CampStay
from .forms import MovementForm
from . import housing
from apps.resources.models import Employee


class MovementEmployeeCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):HousingTests.setUpTestData.__func__(cls)

    def form(self,kind='ARRIVAL',employees=None):
        return MovementForm({'kind':kind,'employees':employees or [self.employee.pk],'date':str(self.day),
            'snapshot':housing.movement_snapshot(self.hr)},company=self.company,camp=self.a,day=self.day)

    def test_only_employee_catalog_is_exposed(self):
        self.client.force_login(self.hr)
        response=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':str(self.day)})
        self.assertNotContains(response,'name="people"')
        self.assertContains(response,'name="employees"')
        self.assertNotContains(response,'Сотрудники кадрового реестра')
        self.assertNotIn('people',response.context['form'].fields)

    def test_arrival_creates_internal_card_and_departure_uses_same_employee(self):
        employee=Employee.objects.create(company=self.company,last_name='Новый',first_name='Сотрудник')
        self.client.force_login(self.hr)
        data={'camp':self.a.pk,'date':str(self.day),'kind':'ARRIVAL','employees':[employee.pk], 'snapshot':housing.movement_snapshot(self.hr)}
        self.assertEqual(self.client.post(reverse('personnel:housing'),data).status_code,302)
        person=Person.objects.get(employee=employee)
        self.assertEqual(person.stays.count(),1)
        data.update(kind='DEPARTURE',snapshot=housing.movement_snapshot(self.hr))
        self.assertEqual(self.client.post(reverse('personnel:housing'),data).status_code,302)
        self.assertEqual(person.stays.count(),1)
        self.assertEqual(person.housing_events.count(),2)

    def test_inactive_present_employee_can_depart_but_cannot_arrive(self):
        self.employee.is_active=False;self.employee.save()
        self.assertTrue(self.form('DEPARTURE').is_valid())
        self.assertFalse(self.form().is_valid())
        self.assertFalse(self.form('DEPARTURE',[self.second_employee.pk]).is_valid())

    def test_transfer_only_present_people_and_requires_target(self):
        form=self.form('TRANSFER');self.assertFalse(form.is_valid());self.assertIn('target',form.errors)
        data=form.data.copy();data['target']=self.b.pk
        self.assertTrue(MovementForm(data,company=self.company,camp=self.a,day=self.day).is_valid())
        data['employees']=[self.second_employee.pk]
        self.assertFalse(MovementForm(data,company=self.company,camp=self.a,day=self.day).is_valid())
