from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company,Role,User
from apps.audit.models import AuditLog
from .models import Board,MovementReviewRequest,Person,StaffingGroup,StaffingSlot
from .recruitment_models import TalentPoolCandidate


class PersonnelAdminCleanupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Очистка персонала')
        cls.admin=User.objects.create_user(username='cleanup-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
        cls.hr=User.objects.create_user(username='cleanup-hr',company=cls.company,role=Role.objects.get(code='HR_HEAD'))
        cls.person=Person.objects.create(company=cls.company,full_name='Тестовый Сотрудник')
        board=Board.objects.create(company=cls.company,name='Тестовая таблица')
        group=StaffingGroup.objects.create(company=cls.company,board=board,position='Рабочий')
        cls.slot=StaffingSlot.objects.create(company=cls.company,group=group,person=cls.person,status='МВ')
        cls.pool=TalentPoolCandidate.objects.create(company=cls.company,full_name='Тестовый Кандидат',created_by=cls.admin)
        cls.movement=MovementReviewRequest.objects.create(company=cls.company,person=cls.person,kind='ARRIVAL',movement_date=timezone.localdate(),requested_by=cls.admin)
        cls.url=reverse('personnel:admin_cleanup')

    def test_only_admin_can_open_cleanup(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.client.force_login(self.admin)
        response=self.client.get(self.url)
        self.assertContains(response,'Администрирование персонала')
        self.assertContains(response,'Пул кандидатов')

    def test_confirmation_is_required(self):
        self.client.force_login(self.admin)
        self.client.post(self.url,{'cleanup_action':'pool','confirmation':'неверно'})
        self.assertTrue(TalentPoolCandidate.objects.filter(pk=self.pool.pk).exists())

    def test_sections_are_cleared_independently_and_audited(self):
        self.client.force_login(self.admin)
        self.client.post(self.url,{'cleanup_action':'pool','confirmation':'ОЧИСТИТЬ'})
        self.assertFalse(TalentPoolCandidate.objects.filter(company=self.company).exists())
        self.assertTrue(MovementReviewRequest.objects.filter(company=self.company).exists())
        self.client.post(self.url,{'cleanup_action':'requests','confirmation':'ОЧИСТИТЬ'})
        self.assertFalse(MovementReviewRequest.objects.filter(company=self.company).exists())
        self.client.post(self.url,{'cleanup_action':'assignments','confirmation':'ОЧИСТИТЬ'})
        self.slot.refresh_from_db();self.person.refresh_from_db()
        self.assertIsNone(self.slot.person_id);self.assertEqual(self.slot.status,'');self.assertIsNone(self.person.schedule_slot_id)
        self.assertEqual(AuditLog.objects.filter(user=self.admin,entity_type='personnel.DemoCleanup').count(),3)
