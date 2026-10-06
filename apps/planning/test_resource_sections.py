from datetime import date
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject
from apps.resources.models import Brigade, EquipmentType
from .models import PlanningWorkspace, GlobalPlanVersion, ResourceMonthAllocation


class ResourceSectionsTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Resource sections')
        self.user = User.objects.create_user(username='resources-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        project = Project.objects.create(company=self.company, name='Project')
        obj = ConstructionObject.objects.create(company=self.company, project=project, name='Object')
        self.workspace = PlanningWorkspace.objects.create(company=self.company, construction_object=obj, name='Period', start_date=date(2026,1,15), end_date=date(2026,3,10))
        self.version = GlobalPlanVersion.objects.create(company=self.company, construction_object=obj, workspace=self.workspace, version_kind='BASELINE', version_number=1, start_date=self.workspace.start_date, end_date=self.workspace.end_date)
        self.workspace.baseline_version = self.version
        self.workspace.save()
        self.brigade = Brigade.objects.create(company=self.company, name='Brigade')
        self.equipment = EquipmentType.objects.create(company=self.company, name='Equipment')
        self.client.force_login(self.user)

    def test_period_addition_is_idempotent_preserves_values_and_splits_sections(self):
        data = {
            'labor': {'period-labor-brigade':self.brigade.pk},
            'equipment': {'period-equipment-equipment_type':self.equipment.pk, 'period-equipment-equipment_number':'A1'},
            'fuel': {'period-fuel-fuel_type':'DIESEL', 'period-fuel-equipment_ref':'A1'},
        }
        for kind, values in data.items():
            url = reverse('planning:workspace_add_resource', args=[self.version.pk,kind])
            self.assertEqual(self.client.post(url, values).status_code, 302)
            self.assertEqual(self.version.resource_allocations.filter(kind=kind).count(), 3)
            row = self.version.resource_allocations.filter(kind=kind).first()
            row.rate = 125
            row.save()
            self.assertEqual(self.client.post(url, values).status_code, 302)
            row.refresh_from_db()
            self.assertEqual(row.rate, 125)
            self.assertEqual(self.version.resource_allocations.filter(kind=kind).count(), 3)
        response = self.client.get(reverse('planning:workspace_edit', args=[self.version.pk]))
        self.assertEqual(response.status_code, 200)
        for section in response.context['resource_sections']:
            self.assertEqual(len(section['forms']), 1)
            self.assertEqual(section['forms'][0]['kind'].value(), section['kind'])
            self.assertContains(response, 'resource-rows-'+section['kind'])
        self.assertNotContains(response, '>Добавить ресурс</button>')

    def test_foreign_resource_and_immutable_version_rejected(self):
        foreign = Company.objects.create(name='Foreign resource')
        brigade = Brigade.objects.create(company=foreign, name='Foreign brigade')
        url = reverse('planning:workspace_add_resource', args=[self.version.pk,'labor'])
        self.assertEqual(self.client.post(url, {'period-labor-brigade':brigade.pk}).status_code, 400)
        self.assertFalse(ResourceMonthAllocation.objects.exists())
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='APPROVED')
        self.assertEqual(self.client.post(url, {'period-labor-brigade':self.brigade.pk}).status_code, 403)

    def test_roles_and_foreign_version(self):
        for code in ('MANAGER','FOREMAN'):
            user = User.objects.create_user(username='resource-'+code, company=self.company, role=Role.objects.get(code=code))
            self.client.force_login(user)
            url = reverse('planning:workspace_add_resource', args=[self.version.pk,'fuel'])
            self.assertEqual(self.client.post(url, {'period-fuel-fuel_type':'DIESEL'}).status_code, 403)
        other = Company.objects.create(name='Other planner')
        user = User.objects.create_user(username='foreign-resource-planner', company=other, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(user)
        self.assertEqual(self.client.post(url, {'period-fuel-fuel_type':'DIESEL'}).status_code, 404)
