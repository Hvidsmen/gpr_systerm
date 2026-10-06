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

    def test_duplicate_code_has_form_error_instead_of_server_error(self):
        Section.objects.create(company=self.company, construction_object=self.obj, code='s', name='Original')
        response = self.client.post(self.url(self.obj), {'code': 's', 'name': 'Duplicate'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('code', response.context['form'].errors)
        self.assertEqual(Section.objects.count(), 1)
        self.assertEqual(self.client.post(self.url(self.other_obj), {'code': 's', 'name': 'Other section'}).status_code, 302)

    def test_update_can_keep_code_but_cannot_duplicate_another_section(self):
        section = Section.objects.create(company=self.company, construction_object=self.obj, code='a', name='Section')
        Section.objects.create(company=self.company, construction_object=self.obj, code='b', name='Other section')
        url = reverse('projects:section_edit', args=[self.obj.pk, section.pk])
        self.assertEqual(self.client.post(url, {'code': 'a', 'name': 'Renamed'}).status_code, 302)
        response = self.client.post(url, {'code': 'b', 'name': 'Conflict'})
        self.assertIn('code', response.context['form'].errors)
        section.refresh_from_db()
        self.assertEqual(section.code, 'a')

    def test_manager_and_foreman_cannot_create_sections(self):
        for code in ('MANAGER', 'FOREMAN'):
            user = User.objects.create_user(username='section-'+code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.post(self.url(self.obj), {'code': 's', 'name': 'Section'}).status_code, 403)
        self.assertFalse(Section.objects.exists())
