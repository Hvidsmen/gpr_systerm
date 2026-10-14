from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse
from apps.projects.models import ConstructionObject
from apps.production.models import DailyFact
from . import test_workspace as fixtures
from .hierarchy_services import create_smg, distribution, smg_warnings
from .workspace_services import build_workspace_snapshot
from .models import SmgWorkMapping


class HierarchyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)
        cls.child = ConstructionObject.objects.create(company=cls.company, project=cls.obj.project, parent=cls.obj, name='КТП-1')
        cls.child2 = ConstructionObject.objects.create(company=cls.company, project=cls.obj.project, parent=cls.obj, name='КТП-2')
    create = fixtures.WorkspaceTests.create
    approve = fixtures.WorkspaceTests.approve

    def test_manual_distribution_mapping_fact_and_nonblocking_excess(self):
        version = self.approve(self.create([self.simple]).baseline_version)
        first = create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: Decimal(200)})
        second = create_smg(self.planner, version, self.child2, fixtures.JAN, {self.simple.pk: Decimal(150)})
        link = SmgWorkMapping.objects.get(workspace=first)
        self.assertEqual(link.smg_work.section.construction_object_id, self.child.pk)
        self.assertEqual(first.parent_gpr_id, version.pk)
        DailyFact.objects.create(company=self.company, project_work=link.smg_work, date=fixtures.JAN, actual_quantity=4)
        workspaces, rows = distribution(version, fixtures.JAN, include_facts=True)
        self.assertEqual(len(workspaces), 2)
        self.assertEqual(rows[0]['gpr'], Decimal(300))
        self.assertEqual(rows[0]['smg'], Decimal(350))
        self.assertEqual(rows[0]['excess'], Decimal(50))
        self.assertEqual(rows[0]['fact'], Decimal(4))
        self.assertTrue(smg_warnings(first))
        snapshot = build_workspace_snapshot(first.baseline_version)
        self.assertEqual(snapshot['parent_gpr_id'], version.pk)
        self.assertTrue(snapshot['warnings'])
        self.approve(first.baseline_version)
        first.baseline_version.refresh_from_db()
        self.assertEqual(first.baseline_version.status, 'APPROVED')

    def test_composite_subworks_are_cloned(self):
        items = list(self.composite.items.all())
        if len(items) > 1:
            items[1].parent = items[0]
            items[1].save()
        version = self.approve(self.create([self.composite]).baseline_version)
        workspace = create_smg(self.planner, version, self.child, fixtures.JAN, {self.composite.pk: 10})
        mapping = workspace.gpr_work_mappings.get()
        self.assertEqual(mapping.smg_work.kind, 'COMPOSITE')
        self.assertEqual(mapping.smg_work.items.count(), self.composite.items.count())
        for item in mapping.smg_work.items.all():
            if item.parent_id:
                self.assertEqual(item.parent.project_work_id, mapping.smg_work_id)
        snapshot = build_workspace_snapshot(workspace.baseline_version)
        self.assertTrue(snapshot['works'])

    def test_invalid_child_duplicate_and_wrong_period_are_rejected(self):
        version = self.approve(self.create([self.simple]).baseline_version)
        with self.assertRaises(ValidationError):
            create_smg(self.planner, version, self.obj, fixtures.JAN, {self.simple.pk: 10})
        create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: 10})
        with self.assertRaises(ValidationError):
            create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: 20})
        with self.assertRaises(ValidationError):
            create_smg(self.planner, version, self.child2, date(2027, 1, 1), {self.simple.pk: 20})
        self.obj.parent = self.child
        with self.assertRaises(ValidationError):
            self.obj.full_clean()

    def test_plan_fact_rolls_up_subobjects_without_double_counting(self):
        from .report_services import build_matrix
        from apps.analytics.revenue import rows_in
        from apps.production.models import LaborFact
        version = self.approve(self.create([self.simple]).baseline_version)
        workspace = create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: 100})
        mapping = workspace.gpr_work_mappings.get()
        DailyFact.objects.create(company=self.company, project_work=mapping.smg_work, date=fixtures.JAN, actual_quantity=4)
        LaborFact.objects.create(company=self.company, construction_object=self.child, brigade=self.brigade, date=fixtures.JAN, actual_workers=3)
        filters = {'start': fixtures.JAN, 'end': date(2026,1,31), 'mode': 'latest', 'sections': ['works', 'labor']}
        report = build_matrix(self.planner, filters)
        self.assertNotIn(self.child.pk, [entry['object'].pk for entry in report['objects']])
        parent = next(entry for entry in report['objects'] if entry['object'].pk == self.obj.pk)
        work = rows_in(parent['sections'][0]['groups'])[0]
        self.assertEqual(work['total']['plan'], Decimal(300))
        self.assertEqual(work['total']['fact'], Decimal(4))
        people = rows_in(parent['sections'][1]['groups'])[0]
        self.assertEqual(people['total']['fact'], Decimal(3))
        report = build_matrix(self.planner, {**filters, 'objects': ConstructionObject.objects.filter(pk=self.child.pk)})
        self.assertEqual(report['objects'][0]['object'].pk, self.child.pk)
        self.assertEqual(rows_in(report['objects'][0]['sections'][0]['groups'])[0]['total']['fact'], Decimal(4))

    def test_foreign_company_and_observer_cannot_create_smg(self):
        from apps.accounts.models import User, Company, Role
        version = self.approve(self.create([self.simple]).baseline_version)
        outsider = User.objects.create_user(username='hierarchy-outsider', company=Company.objects.create(name='Outside'), role=self.planner.role)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse('planning:gpr_distribution_detail', args=[version.pk])).status_code, 404)
        observer = User.objects.create_user(username='hierarchy-observer', company=self.company, role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        self.assertEqual(self.client.get(reverse('planning:smg_create', args=[version.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('planning:gpr_distribution_detail', args=[version.pk])).status_code, 200)

    def test_resource_distribution_and_warning(self):
        from .models import ResourceMonthAllocation
        from .hierarchy_services import resource_distribution
        version = self.approve(self.create([self.simple]).baseline_version)
        workspace = create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: 100})
        ResourceMonthAllocation.objects.create(company=self.company, version=workspace.baseline_version, month=fixtures.JAN, kind='labor', brigade=self.brigade, count=5)
        rows = resource_distribution(version, fixtures.JAN, [workspace])
        self.assertEqual(rows[0]['smg'], Decimal(5))
        self.assertEqual(rows[0]['gpr'], Decimal(0))
        self.assertTrue(smg_warnings(workspace))

    def test_existing_equivalent_subobject_work_is_reused(self):
        from apps.projects.models import Section
        from apps.works.models import ProjectWork
        section = Section.objects.create(company=self.company, construction_object=self.child, name=self.simple.section.name)
        existing = ProjectWork.objects.create(company=self.company, section=section, name=self.simple.name, unit=self.simple.unit, kind=self.simple.kind, load_profile=self.simple.load_profile)
        version = self.approve(self.create([self.simple]).baseline_version)
        workspace = create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: 100})
        self.assertEqual(workspace.gpr_work_mappings.get().smg_work_id, existing.pk)

    def test_manual_daily_editor_updates_monthly_distribution(self):
        version = self.approve(self.create([self.simple]).baseline_version)
        workspace = create_smg(self.planner, version, self.child, fixtures.JAN, {self.simple.pk: 100})
        self.client.force_login(self.planner)
        url = reverse('planning:smg_days_edit', args=[workspace.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = {'form-TOTAL_FORMS': 31, 'form-INITIAL_FORMS': 31, 'form-MAX_NUM_FORMS': 31}
        data.update({f'form-{index}-quantity': 2 for index in range(31)})
        data['form-0-quantity'] = 5
        self.assertEqual(self.client.post(url, data).status_code, 302)
        _, rows = distribution(version, fixtures.JAN)
        self.assertEqual(rows[0]['smg'], Decimal(65))
        self.assertEqual(workspace.baseline_version.work_allocations.get().quantity, Decimal(65))
        self.assertEqual(workspace.baseline_version.work_allocations.get().daily_override[0]['quantity'], '5')
        snapshot = build_workspace_snapshot(workspace.baseline_version)
        self.assertEqual(Decimal(snapshot['works'][0]['daily'][0]['quantity']), Decimal(5))

    def test_distribution_ui_and_creation(self):
        version = self.approve(self.create([self.simple]).baseline_version)
        self.client.force_login(self.planner)
        response = self.client.get(reverse('planning:gpr_distribution_detail', args=[version.pk]), {'month': str(fixtures.JAN)})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(reverse('planning:smg_create', args=[version.pk])+'?month='+str(fixtures.JAN), {'construction_object': self.child.pk, 'form-TOTAL_FORMS': 1, 'form-INITIAL_FORMS': 1, 'form-MAX_NUM_FORMS': 1, 'form-0-work': self.simple.pk, 'form-0-quantity': 100})
        self.assertEqual(response.status_code, 302)
