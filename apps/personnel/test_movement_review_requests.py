from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.resources.models import Employee
from . import housing
from .models import Camp, MovementReviewRequest, Person


class MovementReviewRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Очередь перевахты')
        cls.camp=Camp.objects.create(company=cls.company,name='ВЖГ-1')
        cls.transport=User.objects.create_user(username='transport-review',company=cls.company,role=Role.objects.get(code='TRANSPORT_DISPATCHER'))
        cls.personnel=User.objects.create_user(username='personnel-review',company=cls.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        cls.employee=Employee.objects.create(company=cls.company,last_name='Иванов',first_name='Иван',employee_number='review-1')
        cls.person=Person.objects.create(company=cls.company,employee=cls.employee,full_name=cls.employee.display_name,employee_number=cls.employee.employee_number)

    def test_transport_movement_creates_request_and_personnel_accepts_it(self):
        event_count=self.person.housing_events.count()
        housing.move(self.transport,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        request_row=MovementReviewRequest.objects.get()
        self.assertEqual(request_row.event.person,self.person)
        self.assertEqual(request_row.event.kind,'ARRIVAL')
        self.assertEqual(request_row.status,'OPEN')

        self.client.force_login(self.personnel)
        page=self.client.get(reverse('personnel:housing'))
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Заявки из диспетчерской')
        self.assertContains(page,self.person.display_name)
        self.assertContains(page,'Диспетчерская')
        self.assertContains(page,'Перевахта')
        response=self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk]))
        self.assertRedirects(response,reverse('personnel:housing')+'#movement-requests',fetch_redirect_response=False)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'ACCEPTED')
        self.assertEqual(request_row.reviewed_by,self.personnel)
        self.assertIsNotNone(request_row.reviewed_at)
        self.assertEqual(self.person.housing_events.count(),event_count+1)

    def test_personnel_movement_does_not_create_dispatch_request(self):
        housing.move(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        self.assertFalse(MovementReviewRequest.objects.exists())

    def test_transport_dispatcher_cannot_accept_request(self):
        housing.move(self.transport,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        request_row=MovementReviewRequest.objects.get()
        self.client.force_login(self.transport)
        self.assertEqual(self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk])).status_code,403)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'OPEN')
