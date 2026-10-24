from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role
from apps.planning import test_deletion as fixtures
from apps.production.models import DailyFact
from apps.works.models import ProjectWork
from .models import Section, ConstructionObject


class SectionPurgeTests(TestCase):
    setUpTestData = classmethod(fixtures.PlanningDeletionTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.user)
        self.url = reverse('projects:section_purge', args=[self.obj.pk])

    def confirm(self, response):
        return self.client.post(self.url, {'selection': response.context['selection'], 'confirm_related': 'on'})

    def test_removes_sections_work_and_fact_preserves_object(self):
        Section.objects.create(company=self.company, construction_object=self.obj, name='Пустой', code='empty')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Section.objects.exists())
        self.assertEqual(self.confirm(response).status_code, 302)
        self.assertFalse(Section.objects.filter(construction_object=self.obj).exists())
        self.assertFalse(ProjectWork.objects.exists())
        self.assertFalse(DailyFact.objects.exists())
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists())

    def test_stale_preview_and_missing_checkbox_do_not_delete(self):
        response = self.client.get(self.url)
        self.assertEqual(self.client.post(self.url, {'selection': response.context['selection']}).status_code, 400)
        Section.objects.create(company=self.company, construction_object=self.obj, name='Новый', code='new')
        self.assertEqual(self.confirm(response).status_code, 400)
        self.assertTrue(ProjectWork.objects.exists())

    def test_planner_denied(self):
        user = User.objects.create_user(username='section-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        user.assigned_objects.add(self.obj)
        self.client.force_login(user)
        for url in [self.url, reverse('projects:section_delete', args=[self.obj.pk, self.work.section_id])]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url).status_code, 403)
        self.assertNotContains(self.client.get(reverse('projects:section_list', args=[self.obj.pk])), 'Удалить все разделы объекта')

    def test_single_section_preserves_sibling(self):
        other = Section.objects.create(company=self.company, construction_object=self.obj, name='Оставить', code='keep')
        self.url = reverse('projects:section_delete', args=[self.obj.pk, self.work.section_id])
        self.assertEqual(self.confirm(self.client.get(self.url)).status_code, 302)
        self.assertTrue(Section.objects.filter(pk=other.pk).exists())

    def test_other_object_sections_and_work_preserved(self):
        obj = ConstructionObject.objects.create(company=self.company, project=self.obj.project, name='Другой объект')
        section = Section.objects.create(company=self.company, construction_object=obj, name='Другой раздел')
        work = ProjectWork.objects.create(company=self.company, section=section, name='Сохранить работу', unit='шт')
        self.assertEqual(self.confirm(self.client.get(self.url)).status_code, 302)
        self.assertTrue(Section.objects.filter(pk=section.pk).exists())
        self.assertTrue(ProjectWork.objects.filter(pk=work.pk).exists())

    def test_failure_rolls_back_work_and_plan_deletion(self):
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        response = self.client.get(self.url)
        with patch('apps.projects.purge.AuditLog.objects.create', side_effect=ValidationError('Ошибка аудита')):
            self.assertEqual(self.confirm(response).status_code, 400)
        self.assertTrue(Section.objects.filter(pk=self.work.section_id).exists())
        self.assertTrue(ProjectWork.objects.filter(pk=self.work.pk).exists())
        self.assertTrue(DailyFact.objects.exists())
