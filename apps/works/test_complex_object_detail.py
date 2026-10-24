from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role,User
from . import test_schedule_import as fixtures
from .schedule_import import apply_rows
from .models import ProjectWork


class ComplexObjectDetailTests(TestCase):
    def setUp(self):
        fixtures.ScheduleImportTests.setUp(self)
        apply_rows(self.user,self.section,self.profile,self.rows)
        self.work=ProjectWork.objects.get(section=self.section)
        self.facility=self.work.complex_object
        self.url=reverse('works:complex_object_detail',args=[self.facility.pk])

    def test_detail_shows_full_tree_and_actions(self):
        response=self.client.get(self.url)
        self.assertContains(response,self.facility.name)
        self.assertContains(response,self.work.name)
        for item in self.work.items.all():self.assertContains(response,item.name)
        self.assertContains(response,'Удалить комплексный объект целиком')
        self.assertContains(response,'Изменить название и описание')
        self.assertContains(response,'До комплексных объектов')

    def test_edit_name_preserves_work_and_composition(self):
        response=self.client.post(self.url,{'name':'Новое имя','description':'Описание'})
        self.assertRedirects(response,self.url)
        self.facility.refresh_from_db();self.work.refresh_from_db()
        self.assertEqual(self.facility.name,'Новое имя')
        self.assertEqual(self.work.complex_object_id,self.facility.pk)
        self.assertEqual(self.work.items.count(),2)

    def test_readonly_and_unassigned_scope(self):
        user=User.objects.create_user(username='object-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(user)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'Удалить комплексный объект целиком')
        self.assertEqual(self.client.post(self.url,{'name':'Запрет'}).status_code,403)
        user.role=Role.objects.get(code='PLANNER');user.save()
        self.assertEqual(self.client.get(self.url).status_code,404)
