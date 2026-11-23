from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError

from apps.accounts.models import Company,Role,User
from . import base_cities
from .models import BaseCity,MovementReviewRequest,Person,TalentPoolCandidate


class BaseCityCatalogueTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Справочник городов')
        cls.other=Company.objects.create(name='Другая компания городов')
        cls.admin=User.objects.create_user(username='city-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
        cls.head=User.objects.create_user(username='city-head',company=cls.company,role=Role.objects.get(code='RECRUITMENT_HEAD'))
        cls.recruiter=User.objects.create_user(username='city-recruiter',company=cls.company,role=Role.objects.get(code='RECRUITER'))
        cls.city=BaseCity.objects.create(company=cls.company,name='г. Уфа')
        cls.target=BaseCity.objects.create(company=cls.company,name='Уфа')
        BaseCity.objects.create(company=cls.other,name='Чужой город')
        cls.person=Person.objects.create(company=cls.company,full_name='Иванов Иван')

    def test_personnel_page_is_visible_but_only_managers_can_change_catalogue(self):
        self.client.force_login(self.recruiter)
        page=self.client.get(reverse('personnel:base_city_list'))
        self.assertEqual(page.status_code,200);self.assertContains(page,'Базовые города')
        self.assertNotContains(page,'Добавить город')
        denied=self.client.post(reverse('personnel:base_city_create'),{'name':'Пермь'})
        self.assertEqual(denied.status_code,403)
        self.client.force_login(self.head)
        response=self.client.post(reverse('personnel:base_city_create'),{'name':'  Пермь  '})
        self.assertRedirects(response,reverse('personnel:base_city_list'))
        self.assertTrue(BaseCity.objects.filter(company=self.company,name='Пермь',is_active=True).exists())

    def test_rename_updates_text_routes_and_candidate_source(self):
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат',created_by=self.admin,
            base_city=self.city,source_data={'base_city':'г. Уфа'})
        movement=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',
            movement_date=timezone.localdate(),base_city='г. Уфа')
        base_cities.rename(company=self.company,city_id=self.city.pk,name='Уфа (город)')
        movement.refresh_from_db();candidate.refresh_from_db();self.city.refresh_from_db()
        self.assertEqual(self.city.name,'Уфа (город)')
        self.assertEqual(movement.base_city,'Уфа (город)')
        self.assertEqual(candidate.source_data['base_city'],'Уфа (город)')

    def test_merge_moves_all_references_and_archives_source(self):
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат merge',created_by=self.admin,
            base_city=self.city,source_data={'base_city':'г. Уфа'})
        movement=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',
            movement_date=timezone.localdate(),base_city='г. Уфа')
        base_cities.merge(company=self.company,source_id=self.city.pk,target_id=self.target.pk,
            actor=self.admin,reason='Убираем дубли')
        candidate.refresh_from_db();movement.refresh_from_db();self.city.refresh_from_db()
        self.assertEqual(candidate.base_city,self.target);self.assertEqual(candidate.source_data['base_city'],'Уфа')
        self.assertEqual(movement.base_city,'Уфа')
        self.assertFalse(self.city.is_active);self.assertEqual(self.city.merged_into,self.target)
        self.assertEqual(self.city.status_reason,'Убираем дубли')

    def test_archive_restore_and_safe_delete(self):
        unused=BaseCity.objects.create(company=self.company,name='Удаляемый')
        with self.assertRaisesMessage(ValidationError,'Сначала переместите'):
            base_cities.delete(company=self.company,city_id=unused.pk)
        base_cities.archive(company=self.company,city_id=unused.pk,actor=self.admin,reason='Ошибка')
        unused.refresh_from_db();self.assertFalse(unused.is_active)
        base_cities.restore(company=self.company,city_id=unused.pk)
        unused.refresh_from_db();self.assertTrue(unused.is_active)
        base_cities.archive(company=self.company,city_id=unused.pk,actor=self.admin)
        base_cities.delete(company=self.company,city_id=unused.pk)
        self.assertFalse(BaseCity.objects.filter(pk=unused.pk).exists())

    def test_case_insensitive_duplicate_is_rejected(self):
        with self.assertRaisesMessage(ValidationError,'уже существует'):
            base_cities.create(company=self.company,name='уфа')
