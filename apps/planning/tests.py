from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Company, Role, User
from apps.projects.models import Project, ConstructionObject, Section
from apps.resources.models import Brigade
from apps.works.models import ProjectWork, ProjectWorkItem
from apps.production.models import DailyFact, LaborFact
from apps.production.services import FactService
from core.exceptions import PlanImmutableError, InsufficientPermissionsError, PlanGenerationError
from .models import (
    ProductionCalendar, CalendarDay, LoadProfile, LoadProfileItem,
    MonthlyPlan, PlanVersion, DailyPlan, DailyBaseline,
)
from .services import PlanGeneratorService, PlanWorkflowService, PlanRevisionService


class PlanningWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.company = Company.objects.create(name='Workflow company')
        role = Role.objects.create(code='MANAGER', name='Manager')
        cls.manager = User.objects.create_user(username='manager', company=cls.company, role=role)
        cls.worker = User.objects.create_user(username='worker', company=cls.company)
        project = Project.objects.create(company=cls.company, code='p', name='Project')
        obj = ConstructionObject.objects.create(company=cls.company, project=project, code='o', name='Object')
        section = Section.objects.create(company=cls.company, construction_object=obj, code='s', name='Section')
        cls.work = ProjectWork.objects.create(kind="COMPOSITE", company=cls.company, section=section, code='w', name='Work', unit='m', unit_price=20)
        profile = LoadProfile.objects.create(company=cls.company, code='load', name='Load')
        LoadProfileItem.objects.create(company=cls.company, profile=profile, workday_number=1, percentage=100)
        cls.item = ProjectWorkItem.objects.create(company=cls.company, project_work=cls.work, name='Item', unit='m', load_profile=profile, quantity_per_unit=2, weight=100)
        calendar = ProductionCalendar.objects.create(company=cls.company, code='cal', name='Calendar', year=cls.today.year, is_default=True)
        CalendarDay.objects.create(company=cls.company, calendar=calendar, date=cls.today)
        cls.plan = MonthlyPlan.objects.create(company=cls.company, project_work=cls.work, year=cls.today.year, month=cls.today.month, start_date=cls.today, end_date=cls.today, planned_quantity=10)
        cls.version = PlanVersion.objects.create(company=cls.company, monthly_plan=cls.plan, version_number=1, created_by=cls.manager)
        cls.brigade = Brigade.objects.create(company=cls.company, code='b', name='Brigade')

    def generate_and_approve(self):
        self.assertEqual(PlanGeneratorService.generate(self.version), 1)
        PlanWorkflowService.submit(self.version, self.manager)
        PlanWorkflowService.approve(self.version, self.manager)

    def test_full_workflow_generates_approves_and_completes_without_changing_baseline(self):
        self.generate_and_approve()
        daily_before = list(self.version.daily_plans.values())
        baseline_before = list(DailyBaseline.objects.values())
        self.assertEqual(daily_before[0]['planned_quantity'], Decimal('20'))
        self.assertEqual(daily_before[0]['planned_value'], Decimal('200'))
        approved_at = self.version.approved_at
        self.client.force_login(self.manager)
        response = self.client.post(reverse('planning:version_complete', kwargs={'pk': self.version.pk}))
        self.assertRedirects(response, reverse('planning:version_detail', kwargs={'pk': self.version.pk}))
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'COMPLETED')
        self.assertTrue(self.version.is_immutable)
        self.assertEqual(self.version.approved_at, approved_at)
        self.assertEqual(list(self.version.daily_plans.values()), daily_before)
        self.assertEqual(list(DailyBaseline.objects.values()), baseline_before)
        with self.assertRaises(PlanImmutableError):
            PlanGeneratorService.generate(self.version)

    def test_approved_plan_cannot_be_regenerated(self):
        self.generate_and_approve()
        with self.assertRaises(PlanImmutableError):
            PlanGeneratorService.generate(self.version)
        self.assertEqual(self.version.daily_plans.count(), 1)

    def test_unapproved_plan_cannot_be_completed(self):
        with self.assertRaises(PlanGenerationError):
            PlanWorkflowService.complete(self.version, self.manager)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'DRAFT')

    def test_non_manager_cannot_complete_plan(self):
        self.generate_and_approve()
        self.client.force_login(self.worker)
        self.assertEqual(self.client.post(reverse('planning:version_complete', kwargs={'pk': self.version.pk})).status_code, 403)
        with self.assertRaises(InsufficientPermissionsError):
            PlanWorkflowService.complete(self.version, self.worker)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'APPROVED')

    def test_other_company_cannot_complete_plan(self):
        company = Company.objects.create(name='Other company')
        user = User.objects.create_user(username='other', company=company, role=self.manager.role)
        self.generate_and_approve()
        self.client.force_login(user)
        self.assertEqual(self.client.post(reverse('planning:version_complete', kwargs={'pk': self.version.pk})).status_code, 404)
        with self.assertRaises(InsufficientPermissionsError):
            PlanWorkflowService.complete(self.version, user)

    def test_completion_controls_target_correct_endpoint(self):
        self.generate_and_approve()
        self.client.force_login(self.manager)
        target = reverse('planning:version_complete', kwargs={'pk': self.version.pk})
        for name, pk in [('planning:plan_detail', self.plan.pk), ('planning:version_detail', self.version.pk)]:
            self.assertContains(self.client.get(reverse(name, kwargs={'pk': pk})), target)

    def test_rejected_plan_can_be_resubmitted(self):
        PlanWorkflowService.submit(self.version, self.manager)
        PlanWorkflowService.reject(self.version, self.manager, 'Correct quantities')
        PlanWorkflowService.submit(self.version, self.manager)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'SUBMITTED')

    def test_revision_copies_daily_plan_but_keeps_approved_version_immutable(self):
        self.generate_and_approve()
        revision = PlanRevisionService.create_revision(self.version, self.manager)
        self.assertEqual(revision.status, 'DRAFT')
        self.assertEqual(revision.version_number, 2)
        self.assertFalse(revision.is_baseline)
        self.assertEqual(revision.daily_plans.get().planned_quantity, self.version.daily_plans.get().planned_quantity)
        self.assertEqual(DailyBaseline.objects.count(), 1)

    def test_missing_author_is_displayed_without_template_error(self):
        self.version.created_by = None
        self.version.save(update_fields=['created_by'])
        self.client.force_login(self.manager)
        for name, kwargs in [('planning:plan_detail', {'pk': self.plan.pk}), ('planning:plan_versions', {'plan_pk': self.plan.pk})]:
            self.assertEqual(self.client.get(reverse(name, kwargs=kwargs)).status_code, 200)

    def test_fact_services_use_current_model_fields_and_assign_company(self):
        fact = FactService.record_daily_fact(self.work, self.item, self.today, Decimal('3'), self.manager)
        self.assertEqual(fact.company_id, self.company.pk)
        self.assertEqual(fact.actual_value, Decimal('30'))
        repeated = FactService.record_daily_fact(self.work, self.item, self.today, Decimal('4'), self.manager)
        self.assertEqual(repeated.pk, fact.pk)
        self.assertEqual(DailyFact.objects.count(), 1)
        labor = FactService.record_labor_fact(self.work.section.construction_object, self.today, self.brigade, 5, 4, Decimal('40'), Decimal('32'), Decimal('350'))
        self.assertEqual(labor.company_id, self.company.pk)
        self.assertEqual(labor.planned_workers, 5)
        self.assertEqual(labor.actual_workers, 4)
        self.assertEqual(LaborFact.objects.count(), 1)
