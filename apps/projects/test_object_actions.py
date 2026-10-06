from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from .models import Project, ConstructionObject, Section


class ObjectActionsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Object actions')
        self.user = User.objects.create_user(username='object-editor', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.project = Project.objects.create(company=self.company, code='p', name='Project')
        self.obj = ConstructionObject.objects.create(company=self.company, project=self.project, code='o', name='Object')
        self.client.force_login(self.user)

    def test_edit_and_confirmed_delete(self):
        edit = reverse('projects:object_edit', args=[self.obj.pk])
        delete = reverse('projects:object_delete', args=[self.obj.pk])
        for page in (reverse('projects:object_list', args=[self.project.pk]), reverse('projects:project_detail', args=[self.project.pk])):
            self.assertContains(self.client.get(page), edit)
            self.assertContains(self.client.get(page), delete)
        self.assertEqual(self.client.post(edit, {'code': 'o', 'name': 'Updated', 'parent': ''}).status_code, 302)
        self.obj.refresh_from_db()
        self.assertEqual(self.obj.name, 'Updated')
        self.assertContains(self.client.get(delete), 'Подтвердить удаление')
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists())
        self.assertEqual(self.client.post(delete).status_code, 302)
        self.assertFalse(ConstructionObject.objects.filter(pk=self.obj.pk).exists())

    def test_related_data_blocks_deletion(self):
        section = Section.objects.create(company=self.company, construction_object=self.obj, code='s', name='Section')
        self.assertEqual(self.client.post(reverse('projects:object_delete', args=[self.obj.pk])).status_code, 400)
        self.assertTrue(Section.objects.filter(pk=section.pk).exists())
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists())

    def test_parent_scope_cycles_and_duplicate_codes(self):
        other_project = Project.objects.create(company=self.company, code='other', name='Other')
        other = ConstructionObject.objects.create(company=self.company, project=other_project, code='other', name='Other')
        child = ConstructionObject.objects.create(company=self.company, project=self.project, parent=self.obj, code='child', name='Child')
        edit = reverse('projects:object_edit', args=[self.obj.pk])
        for parent in (other.pk, self.obj.pk, child.pk):
            self.assertEqual(self.client.post(edit, {'code': 'o', 'name': 'Object', 'parent': parent}).status_code, 200)
            self.obj.refresh_from_db()
            self.assertIsNone(self.obj.parent_id)
        create = reverse('projects:object_create', args=[self.project.pk])
        self.assertEqual(self.client.post(create, {'code': 'o', 'name': 'Duplicate'}).status_code, 200)
        self.assertEqual(self.client.post(create, {'code': 'new', 'name': 'New', 'parent': self.obj.pk}).status_code, 302)

    def test_company_and_roles(self):
        foreign = Company.objects.create(name='Foreign')
        outsider = User.objects.create_user(username='outsider', company=foreign, role=self.user.role)
        self.client.force_login(outsider)
        for name in ('object_edit', 'object_delete'):
            self.assertEqual(self.client.get(reverse('projects:'+name, args=[self.obj.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('projects:'+name, args=[self.obj.pk])).status_code, 404)
        for code in ('MANAGER', 'FOREMAN'):
            user = User.objects.create_user(username=code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            for name in ('object_edit', 'object_delete'):
                self.assertEqual(self.client.post(reverse('projects:'+name, args=[self.obj.pk])).status_code, 403)
