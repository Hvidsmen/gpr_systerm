from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from .models import Project, ConstructionObject, Section


class SectionCreationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Sections')
        cls.user = User.objects.create_user(username='sections-planner', company=cls.company, role=Role.objects.get(code='PLANNER'))
        project = Project.objects.create(company=cls.company, code='p', name='Project')
        cls.obj = ConstructionObject.objects.create(company=cls.company, project=project, code='o', name='Object')
        cls.other_obj = ConstructionObject.objects.create(company=cls.company, project=project, code='o2', name='Second object')
        foreign = Company.objects.create(name='Foreign sections')
        foreign_project = Project.objects.create(company=foreign, code='p', name='Foreign project')
        cls.foreign_obj = ConstructionObject.objects.create(company=foreign, project=foreign_project, code='o', name='Foreign object')

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, obj):
        return reverse('projects:section_create', args=[obj.pk])

    def test_create_assigns_object_from_url_and_company_and_redirects(self):
        response = self.client.post(self.url(self.obj), {
            'code': 's', 'name': 'Section',
            'construction_object': self.foreign_obj.pk,
            'company': self.foreign_obj.company_id,
        })
        self.assertRedirects(response, reverse('projects:section_list', args=[self.obj.pk]))
        section = Section.objects.get()
        self.assertEqual(section.construction_object, self.obj)
        self.assertEqual(section.company, self.company)

    def test_foreign_object_is_rejected_on_get_and_post(self):
        self.assertEqual(self.client.get(self.url(self.foreign_obj)).status_code, 404)
        self.assertEqual(self.client.post(self.url(self.foreign_obj), {'code': 's', 'name': 'Section'}).status_code, 404)
        self.assertFalse(Section.objects.exists())

    def test_submitted_code_is_ignored_and_new_section_gets_automatic_code(self):
        Section.objects.create(company=self.company, construction_object=self.obj, code='s', name='Original')
        response = self.client.post(self.url(self.obj), {'code': 's', 'name': 'New'})
        self.assertEqual(response.status_code, 302)
        section = Section.objects.get(name='New')
        self.assertEqual(section.code, 'SEC-000001')
        self.assertNotIn('code', self.client.get(self.url(self.obj)).context['form'].fields)

    def test_update_preserves_existing_code(self):
        section = Section.objects.create(company=self.company, construction_object=self.obj, code='a', name='Section')
        Section.objects.create(company=self.company, construction_object=self.obj, code='b', name='Other section')
        url = reverse('projects:section_edit', args=[self.obj.pk, section.pk])
        self.assertEqual(self.client.post(url, {'code': 'b', 'name': 'Renamed'}).status_code, 302)
        section.refresh_from_db()
        self.assertEqual(section.code, 'a')
        self.assertEqual(section.name, 'Renamed')

    def test_manager_and_foreman_cannot_create_sections(self):
        for code in ('MANAGER', 'FOREMAN'):
            user = User.objects.create_user(username='section-'+code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url(self.obj), {'code': 's', 'name': 'Section'}).status_code, 403)
        self.assertFalse(Section.objects.exists())

    def test_delete_confirmation_renders_cancel_link_and_deletes_only_on_post(self):
        section = Section.objects.create(company=self.company, construction_object=self.obj,
                                         code='delete-section', name='Section to delete')
        keep = Section.objects.create(company=self.company, construction_object=self.obj,
                                      code='keep-section', name='Section to keep')
        url = reverse('projects:section_delete', args=[self.obj.pk, section.pk])
        back = reverse('projects:section_list', args=[self.obj.pk])
        response = self.client.get(url)
        self.assertContains(response, f'href="{back}"')
        self.assertContains(response, 'Отмена')
        self.assertTrue(Section.objects.filter(pk=section.pk).exists())
        self.assertRedirects(self.client.post(url), back)
        self.assertFalse(Section.objects.filter(pk=section.pk).exists())
        self.assertTrue(Section.objects.filter(pk=keep.pk).exists())
