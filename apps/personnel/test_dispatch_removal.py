from django.test import TestCase
from django.urls import reverse

from .models import StaffingSlot
from .forms import MovementForm
from .plan_assignments import AssignmentForm
from .rotation import rotation_rows


class DispatchRemovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.client.force_login(self.hr)
        self.url=reverse('personnel:remove_from_dispatch',args=[self.person.pk])

    def test_unassigned_person_can_be_removed_without_deleting_history(self):
        page=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk,'date':self.day})
        self.assertContains(page,self.url)
        stay_count=self.person.stays.count()
        self.assertEqual(self.client.post(self.url,{'reason':'Ошибочно добавлен'}).status_code,302)
        self.person.refresh_from_db()
        self.assertIsNotNone(self.person.dispatcher_removed_at)
        self.assertEqual(self.person.dispatcher_removed_by,self.hr)
        self.assertEqual(self.person.dispatcher_removal_reason,'Ошибочно добавлен')
        self.assertEqual(self.person.stays.count(),stay_count)
        self.assertFalse(any(row.get('person_id')==self.person.pk for row in rotation_rows(self.company,[self.a],self.day)[0]))
        assignment=AssignmentForm(company=self.company)
        self.assertFalse(assignment.fields['employee'].queryset.filter(pk=self.employee.pk).exists())
        movement=MovementForm(company=self.company,camp=self.a,day=self.day)
        self.assertFalse(movement.fields['employees'].queryset.filter(pk=self.employee.pk).exists())

    def test_reason_is_required_and_active_assignment_blocks_removal(self):
        self.assertEqual(self.client.post(self.url,{'reason':''}).status_code,200)
        self.person.refresh_from_db();self.assertIsNone(self.person.dispatcher_removed_at)
        StaffingSlot.objects.filter(pk=self.slot.pk).update(planning_position=self.position.planning_mapping.brigade)
        response=self.client.post(self.url,{'reason':'Не нужен'})
        self.assertContains(response,'Сначала удалите действующее назначение')
        self.person.refresh_from_db();self.assertIsNone(self.person.dispatcher_removed_at)
