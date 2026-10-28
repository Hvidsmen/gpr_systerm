from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from apps.accounts.models import Role,User
from . import test_workspace as fixtures
from . import estimate_review as service
from .global_services import GlobalPlanService
from .approval_test_helpers import departments
from .models import GlobalPlanDecision


class EstimateReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)
        cls.estimator=User.objects.create_user(username='estimate-reviewer',company=cls.company,role=Role.objects.get(code='ESTIMATOR'))
        cls.other=User.objects.create_user(username='other-estimate-reviewer',company=cls.company,role=Role.objects.get(code='ESTIMATOR'))
    create=fixtures.WorkspaceTests.create

    def prepare(self):
        from .models import LoadProfile,LoadProfileItem
        profile=LoadProfile.objects.create(company=self.company,name='Review profile')
        LoadProfileItem.objects.create(company=self.company,profile=profile,workday_number=1,percentage=100)
        self.simple.load_profile=profile;self.simple.save()
        self.version=self.create(works=[self.simple]).baseline_version
        from .preparation import handoff,ready
        handoff(self.planner,self.version,self.manager,self.estimator)
        ready(self.manager,self.version)
        self.task=self.version.estimate_reviews.order_by('-pk').first()

    def checked(self):
        for wid in service.work_ids(self.version):service.review_work(self.estimator,self.task,wid,True)
        return service.finish(self.estimator,self.task,True)

    def test_parallel_approval_and_final_gate(self):
        self.prepare()
        with self.assertRaises(ValidationError):GlobalPlanService.transition(self.version,self.manager,'submit')
        with self.assertRaises(ValidationError):departments(self.version,self.approvers)
        with self.assertRaises(ValidationError):GlobalPlanService.transition(self.version,self.approvers['CEO'],'approve')
        self.checked()
        self.version=GlobalPlanService.transition(self.version,self.manager,'submit')
        self.version=departments(self.version,self.approvers)
        self.assertTrue(GlobalPlanDecision.objects.filter(review__version=self.version,section='ESTIMATE',action='APPROVE').exists())
        approved=GlobalPlanService.transition(self.version,self.approvers['CEO'],'approve')
        self.assertEqual(approved.status,'APPROVED')

    def test_price_and_name_changes_return_submitted_plan_without_mutating_frozen_review(self):
        self.prepare();self.checked();self.version=GlobalPlanService.transition(self.version,self.manager,'submit')
        self.task=service.assign(self.planner,self.version,self.estimator)
        frozen=self.version.review_rounds.get().snapshot
        service.change(self.estimator,self.task,self.simple.pk,name='Проверенное название',price=Decimal('12.50'),effective_from=date(2027,1,1),reason='Проверка сметы')
        self.version.refresh_from_db();self.assertEqual(self.version.status,'REJECTED')
        self.assertEqual(self.version.review_rounds.get().snapshot,frozen)
        self.assertEqual(self.simple.price_history.order_by('-pk').first().price,Decimal('12.50'))
        self.assertEqual(self.task.events.filter(action='CHANGE').count(),1)

    def test_scope_and_rejection_and_invalidated_signature(self):
        self.prepare()
        with self.assertRaises(PermissionDenied):service.review_work(self.other,self.task,self.simple.pk,True)
        with self.assertRaises(PermissionDenied):service.change(self.estimator,self.task,self.composite.pk,name='No')
        service.review_work(self.estimator,self.task,self.simple.pk,False,'Используйте старую работу из справочника')
        with self.assertRaises(ValidationError):service.finish(self.estimator,self.task,True)
        self.checked();service.require_approved(self.version)
        row=self.version.work_allocations.first();row.quantity+=1;row.save()
        with self.assertRaises(ValidationError):service.require_approved(self.version)

    def test_ui_and_estimator_cannot_change_planning_or_assign_self(self):
        self.prepare();self.client.force_login(self.estimator)
        self.assertContains(self.client.get(reverse('planning:estimate_review_detail',args=[self.task.pk])),'Название, цена и состав проверены')
        self.assertEqual(self.client.get(reverse('planning:workspace_edit',args=[self.version.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('planning:workspace_edit',args=[self.version.pk]),{}).status_code,403)
        self.assertEqual(self.client.post(reverse('planning:estimate_review_assign',args=['gpr',self.version.pk]),{'estimator':self.estimator.pk}).status_code,403)

    def test_replace_duplicate_preserves_monthly_quantities(self):
        from apps.works.models import ProjectWork
        from .models import WorkMonthAllocation
        self.prepare()
        duplicate=ProjectWork.objects.create(company=self.company,section=self.simple.section,name='Existing catalog work',unit=self.simple.unit,unit_price=10,load_profile=self.simple.load_profile)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=duplicate,month=fixtures.JAN,quantity=50)
        service.replace_work(self.estimator,self.task,self.simple.pk,duplicate.pk)
        self.assertFalse(self.version.work_allocations.filter(work=self.simple).exists())
        self.assertEqual(self.version.work_allocations.get(work=duplicate,month=fixtures.JAN).quantity,350)
        self.assertEqual(sum(self.version.work_allocations.values_list('quantity',flat=True)),1050)
        self.assertTrue(self.simple.__class__.objects.filter(pk=self.simple.pk).exists())
