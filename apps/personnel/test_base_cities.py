from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.apps import apps as django_apps
from importlib import import_module

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
        cls.city=BaseCity.objects.create(company=cls.company,name='Старое название Уфы')
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
            base_city=self.city,source_data={'base_city':'Старое название Уфы'})
        movement=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',
            movement_date=timezone.localdate(),base_city='Старое название Уфы')
        base_cities.rename(company=self.company,city_id=self.city.pk,name='Уфа (город)')
        movement.refresh_from_db();candidate.refresh_from_db();self.city.refresh_from_db()
        self.assertEqual(self.city.name,'Уфа (город)')
        self.assertEqual(movement.base_city,'Уфа (город)')
        self.assertEqual(candidate.source_data['base_city'],'Уфа (город)')

    def test_merge_moves_all_references_and_archives_source(self):
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат merge',created_by=self.admin,
            base_city=self.city,source_data={'base_city':'Старое название Уфы'})
        movement=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',
            movement_date=timezone.localdate(),base_city='Старое название Уфы')
        base_cities.merge(company=self.company,source_id=self.city.pk,target_id=self.target.pk,
            actor=self.admin,reason='Убираем дубли')
        candidate.refresh_from_db();movement.refresh_from_db();self.city.refresh_from_db()
        self.assertEqual(candidate.base_city,self.target);self.assertEqual(candidate.source_data['base_city'],'Уфа')
        self.assertEqual(movement.base_city,'Уфа')
        self.assertFalse(self.city.is_active);self.assertEqual(self.city.merged_into,self.target)
        self.assertEqual(self.city.status_reason,'Убираем дубли')

    def test_merge_page_accepts_city_name_from_single_searchable_list(self):
        source=BaseCity.objects.create(company=self.company,name='Временный город')
        self.client.force_login(self.admin)
        response=self.client.post(reverse('personnel:base_city_merge',args=[source.pk]),{'target_name':'г. Уфа','reason':'Убираем дубль'})
        self.assertRedirects(response,reverse('personnel:base_city_list'))
        source.refresh_from_db();self.assertEqual(source.merged_into,self.target);self.assertFalse(source.is_active)

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

    def test_city_prefix_is_removed_for_service_and_direct_model_writes(self):
        city=base_cities.create(company=self.company,name='  г. Казань  ')
        direct=BaseCity.objects.create(company=self.company,name='г Омск')
        self.assertEqual(city.name,'Казань');self.assertEqual(direct.name,'Омск')
        with self.assertRaisesMessage(ValidationError,'уже существует'):
            base_cities.create(company=self.company,name='г Казань')

    def test_seed_adds_full_catalogue_merges_prefixed_duplicate_and_is_idempotent(self):
        prefixed=BaseCity(company=self.company,name='г. Уфа')
        BaseCity.objects.bulk_create([prefixed])
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат seed',created_by=self.admin,
            base_city=prefixed,source_data={'base_city':'г. Уфа'})
        movement=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',
            movement_date=timezone.localdate(),base_city='г. Уфа')
        migration=import_module('apps.personnel.migrations.0069_seed_russian_base_cities')
        migration.seed_cities(django_apps,None)
        candidate.refresh_from_db();movement.refresh_from_db()
        self.assertEqual(candidate.base_city,self.target)
        self.assertEqual(candidate.source_data['base_city'],'Уфа');self.assertEqual(movement.base_city,'Уфа')
        self.assertFalse(BaseCity.objects.filter(company=self.company,name='г. Уфа').exists())
        names=set(BaseCity.objects.filter(company=self.company).values_list('name',flat=True))
        self.assertTrue({'Москва','Санкт-Петербург','Владивосток','Калининград','Якутск'}<=names)
        self.assertGreaterEqual(len(names),1111)
        count=BaseCity.objects.count();migration.seed_cities(django_apps,None);self.assertEqual(BaseCity.objects.count(),count)
        self.client.force_login(self.admin)
        page=self.client.get(reverse('personnel:base_city_list'))
        self.assertEqual(len(page.context['cities']),50);self.assertGreater(page.context['city_page'].paginator.num_pages,1)

    def test_restore_excel_city_migration_links_existing_candidate(self):
        city=BaseCity.objects.create(company=self.company,name='Челябинск')
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Город остался в Excel',
            source_data={'base_city':'г. Челябинск'},created_by=self.recruiter)
        migration=import_module('apps.personnel.migrations.0073_restore_excel_base_cities')
        migration.restore_excel_base_cities(django_apps,None)
        candidate.refresh_from_db()
        self.assertEqual(candidate.base_city,city)
        self.assertEqual(candidate.source_data['base_city'],'Челябинск')
