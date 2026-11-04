from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role,User
from apps.planning.models import WorkTask
from apps.planning.task_services import visible
from .models import ScheduleChangeRequest,ZupWorkSchedule
from .rotation import rotation_rows
from .schedule_changes import reconcile_company


class ScheduleChangeWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)
        cls.current=ZupWorkSchedule.objects.create(company=cls.company,name='Вахта 1.1М (60х30)')
        cls.requested=ZupWorkSchedule.objects.create(company=cls.company,name='Вахта 1.2М (45х45)')
        cls.inactive=ZupWorkSchedule.objects.create(company=cls.company,name='Старый график',is_active=False)
        cls.catalogue_user=User.objects.create_user(username='zup-schedule-manager',company=cls.company,role=Role.objects.get(code='PERSONNEL_CATALOG_MANAGER'))

    def setUp(self):
        self.client.force_login(self.hr)
        self.employee.zup_card={'schedule':self.current.name};self.employee.save()

    def test_request_creates_marker_and_catalogue_manager_task_then_sync_applies(self):
        url=reverse('personnel:person_schedule',args=[self.person.pk])
        page=self.client.get(url)
        self.assertContains(page,self.current.name);self.assertContains(page,self.requested.name);self.assertNotContains(page,self.inactive.name)
        response=self.client.post(url,{'schedule_action':'request_zup','schedule':self.requested.pk})
        self.assertRedirects(response,url,fetch_redirect_response=False)
        row=ScheduleChangeRequest.objects.get(person=self.person,status='PENDING')
        task=WorkTask.objects.get(key=row.task_key)
        self.assertEqual(task.target_role,'PERSONNEL_CATALOG_MANAGER');self.assertEqual(task.status,'OPEN')
        self.assertTrue(visible(self.catalogue_user).filter(pk=task.pk).exists())
        roster=rotation_rows(self.company,[self.a],self.day)[0]
        person_row=next(item for item in roster if item.get('person') and item['person'].pk==self.person.pk)
        self.assertEqual(person_row['schedule_change_pending'],row)
        self.slot.refresh_from_db();self.assertEqual(self.slot.pattern,self.pattern)
        self.employee.zup_card={'schedule':self.requested.name};self.employee.save()
        self.assertEqual(reconcile_company(self.company),1)
        row.refresh_from_db();task.refresh_from_db();self.slot.refresh_from_db()
        self.assertEqual(row.status,'APPLIED');self.assertEqual(task.status,'DONE');self.assertIsNone(self.slot.pattern)
        self.assertEqual(reconcile_company(self.company),0)

    def test_same_schedule_is_rejected_and_new_request_replaces_pending(self):
        url=reverse('personnel:person_schedule',args=[self.person.pk])
        response=self.client.post(url,{'schedule_action':'request_zup','schedule':self.current.pk})
        self.assertContains(response,'уже установлен этот график')
        self.client.post(url,{'schedule_action':'request_zup','schedule':self.requested.pk})
        third=ZupWorkSchedule.objects.create(company=self.company,name='Вахта 2.1М (30х30)')
        self.client.post(url,{'schedule_action':'request_zup','schedule':third.pk})
        self.assertEqual(ScheduleChangeRequest.objects.filter(person=self.person,status='PENDING').count(),1)
        self.assertEqual(ScheduleChangeRequest.objects.filter(person=self.person,status='CANCELLED').count(),1)
