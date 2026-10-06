from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork, WorkTemplate
from apps.planning.models import LoadProfile, ProductionCalendar
from apps.resources.models import Position, Brigade, FuelType
from apps.production.models import DeviationReason
from .models import CodeSequence


class AutomaticCodeTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Automatic codes')
        self.project = Project.objects.create(company=self.company, name='Project')
        self.obj = ConstructionObject.objects.create(company=self.company, project=self.project, name='Object')
        self.section = Section.objects.create(company=self.company, construction_object=self.obj, name='Section')

    def test_all_business_codes_are_generated_and_stable(self):
        rows = [self.project, self.obj, self.section]
        profile = LoadProfile.objects.create(company=self.company, name='Profile')
        rows += [profile,
            ProjectWork.objects.create(company=self.company, section=self.section, name='Work', unit='м', load_profile=profile),
            WorkTemplate.objects.create(company=self.company, name='Template', unit='м'),
            ProductionCalendar.objects.create(company=self.company, name='Calendar', year=2026),
            Position.objects.create(company=self.company, name='Position'),
            Brigade.objects.create(company=self.company, name='Brigade'),
            FuelType.objects.create(company=self.company, name='Fuel'),
            DeviationReason.objects.create(company=self.company, name='Reason'),
        ]
        for row in rows:
            self.assertRegex(row.code, r'^[A-Z]+-000001$')
            code = row.code
            row.name = 'Updated'
            row.save()
            row.refresh_from_db()
            self.assertEqual(row.code, code)

    def test_legacy_collision_and_deleted_number_are_not_reused(self):
        Project.objects.create(company=self.company, name='Legacy', code='PRJ-000002')
        project = Project.objects.create(company=self.company, name='Next')
        self.assertEqual(project.code, 'PRJ-000003')
        project.delete()
        self.assertEqual(Project.objects.create(company=self.company, name='After deletion').code, 'PRJ-000004')
        other = Company.objects.create(name='Other codes')
        self.assertEqual(Project.objects.create(company=other, name='Other').code, 'PRJ-000001')

    def test_form_ignores_supplied_code_and_keeps_existing_code(self):
        user = User.objects.create_user(username='code-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(user)
        url = reverse('projects:object_create', args=[self.project.pk])
        self.assertNotContains(self.client.get(url), 'name="code"')
        self.assertEqual(self.client.post(url, {'name':'Created', 'code':'forged'}).status_code, 302)
        obj = ConstructionObject.objects.get(name='Created')
        self.assertEqual(obj.code, 'OBJ-000002')
        self.assertEqual(self.client.post(reverse('projects:object_edit', args=[obj.pk]), {'name':'Edited', 'code':'changed'}).status_code, 302)
        obj.refresh_from_db()
        self.assertEqual(obj.code, 'OBJ-000002')

    def test_transaction_failure_does_not_consume_number(self):
        from django.db import transaction
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                Project.objects.create(company=self.company, name='Rolled back')
                raise RuntimeError('Rollback')
        self.assertEqual(Project.objects.create(company=self.company, name='After rollback').code, 'PRJ-000002')
