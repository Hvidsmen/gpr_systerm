from datetime import date
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from apps.accounts.models import User,Role,Company
from apps.projects.models import ConstructionObject
from apps.production.models import DailyFact
from apps.works.models import ProjectWork
from . import test_deletion as fixtures
from .models import PlanningWorkspace,GlobalPlanVersion,DailySchedule,DailyScheduleEntry,GlobalPlanReview,ProjectPlanVersion,ProjectPlanMember
from .deletion_graph import DeletionGraph

class CascadingPlanDeletionTests(TestCase):
    setUpTestData=classmethod(fixtures.PlanningDeletionTests.setUpTestData.__func__)
    def setUp(self):
        self.admin=User.objects.create_user(username='purge-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(self.admin)
        self.url=reverse('planning:workspace_delete',args=[self.workspace.pk])

    def revision(self):
        return GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,version_number=2,start_date=self.workspace.start_date,end_date=self.workspace.end_date,previous_version=self.base)

    def confirm(self):
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        return self.client.post(self.url,{'confirm_history':'on','preview_token':response.context['preview_token']})

    def test_external_revision_is_previewed_and_deleted_with_root(self):
        revision=self.revision()
        preview=self.client.get(self.url)
        self.assertContains(preview,'ID '+str(revision.pk))
        self.assertTrue(GlobalPlanVersion.objects.filter(pk=revision.pk).exists())
        self.assertEqual(self.confirm().status_code,302)
        self.assertFalse(PlanningWorkspace.objects.exists());self.assertFalse(GlobalPlanVersion.objects.exists())
        self.assertTrue(DailyFact.objects.filter(pk=self.fact.pk).exists())
        self.assertTrue(ProjectWork.objects.filter(pk=self.work.pk).exists())
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists())

    def test_applied_smg_and_rebased_revisions_are_deleted(self):
        first=DailySchedule.objects.create(company=self.company,construction_object=self.obj,source=self.base,month=date(2026,10,1),revision=1,status='APPLIED',created_by=self.admin)
        second=DailySchedule.objects.create(company=self.company,construction_object=self.obj,replaces=first,month=date(2026,10,1),revision=2,created_by=self.admin)
        DailyScheduleEntry.objects.create(schedule=second,key='work:1',kind='work',label='Работа',metadata={},rows=[])
        independent=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=date(2026,11,1),revision=1,created_by=self.admin)
        self.assertEqual(self.confirm().status_code,302)
        self.assertEqual(list(DailySchedule.objects.values_list('pk',flat=True)),[independent.pk])
        self.assertFalse(DailyScheduleEntry.objects.exists())
        self.assertTrue(DailyFact.objects.exists())

    def test_dependent_workspace_is_deleted_but_unrelated_workspace_is_preserved(self):
        child=PlanningWorkspace.objects.create(company=self.company,construction_object=self.obj,name='Зависимое продолжение',start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        version=GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,workspace=child,version_kind='BASELINE',version_number=2,start_date=self.workspace.start_date,end_date=self.workspace.end_date,previous_version=self.base)
        PlanningWorkspace.objects.filter(pk=child.pk).update(baseline_version=version)
        other=PlanningWorkspace.objects.create(company=self.company,construction_object=self.obj,name='Независимый',start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        self.assertEqual(self.confirm().status_code,302)
        self.assertEqual(list(PlanningWorkspace.objects.values_list('pk',flat=True)),[other.pk])

    def test_fixed_project_plan_is_deleted_without_other_source_plan(self):
        other_object=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой объект сводного плана')
        other=GlobalPlanVersion.objects.create(company=self.company,construction_object=other_object,version_number=1,start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        GlobalPlanVersion.objects.filter(pk=other.pk).update(status='APPROVED');other.refresh_from_db()
        project_plan=ProjectPlanVersion.objects.create(company=self.company,project=self.obj.project,title='Сводный',version_number=1,start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        GlobalPlanVersion.objects.filter(pk=self.base.pk).update(status='APPROVED')
        self.base.refresh_from_db()
        ProjectPlanMember.objects.create(company=self.company,consolidated_version=project_plan,construction_object=self.obj,version=self.base)
        ProjectPlanMember.objects.create(company=self.company,consolidated_version=project_plan,construction_object=other_object,version=other)
        ProjectPlanVersion.objects.filter(pk=project_plan.pk).update(status='FIXED')
        self.assertEqual(self.confirm().status_code,302)
        self.assertFalse(ProjectPlanVersion.objects.exists());self.assertFalse(ProjectPlanMember.objects.exists())
        self.assertTrue(GlobalPlanVersion.objects.filter(pk=other.pk).exists())

    def test_review_macro_carryover_and_rotation_dependencies_are_removed(self):
        from .models import MacroHistorySource,WorkCarryover,CarryoverReviewTask
        from apps.rotation.models import RotationPlan
        review=GlobalPlanReview.objects.create(company=self.company,version=self.base,number=1,snapshot={'works':[]},submitted_by=self.admin,legacy_approved=True)
        macro=PlanningWorkspace.objects.create(company=self.company,construction_object=self.obj,name='Связанный макроплан',start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        MacroHistorySource.objects.create(company=self.company,workspace=macro,review=review,construction_object=self.obj,start=self.workspace.start_date,end=self.workspace.end_date)
        dependent=GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,version_number=2,start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        WorkCarryover.objects.create(company=self.company,version=dependent,source_version=self.base,month=self.workspace.start_date,work=self.work,quantity=2,imported_by=self.admin)
        CarryoverReviewTask.objects.create(company=self.company,version=dependent,source_version=self.base,month=self.workspace.start_date,due_date=self.workspace.end_date,assigned_to=self.admin)
        RotationPlan.objects.create(company=self.company,source=self.base,title='Связанный график',start=self.workspace.start_date,end=self.workspace.end_date)
        self.assertEqual(self.confirm().status_code,302)
        self.assertFalse(GlobalPlanReview.objects.exists());self.assertFalse(MacroHistorySource.objects.exists())
        self.assertFalse(WorkCarryover.objects.exists());self.assertFalse(CarryoverReviewTask.objects.exists());self.assertFalse(RotationPlan.objects.exists())
        self.assertFalse(GlobalPlanVersion.objects.exists());self.assertFalse(PlanningWorkspace.objects.exists())
        self.assertTrue(DailyFact.objects.exists())

    def test_planner_can_confirm_deletion_of_assigned_draft_chain(self):
        self.revision();self.client.force_login(self.user)
        self.assertEqual(self.confirm().status_code,302)
        self.assertFalse(GlobalPlanVersion.objects.exists())
        self.assertTrue(DailyFact.objects.exists())

    def test_changed_dependency_graph_requires_new_preview(self):
        self.revision();token=self.client.get(self.url).context['preview_token']
        GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,version_number=3,start_date=self.workspace.start_date,end_date=self.workspace.end_date,previous_version=self.base)
        response=self.client.post(self.url,{'confirm_history':'on','preview_token':token})
        self.assertEqual(response.status_code,400)
        self.assertContains(response,'обновлённый список',status_code=400)
        self.assertTrue(PlanningWorkspace.objects.exists());self.assertEqual(GlobalPlanVersion.objects.count(),3)

    def test_foreign_company_dependency_blocks_whole_operation(self):
        foreign=Company.objects.create(name='Foreign graph')
        version=self.revision()
        GlobalPlanVersion.objects.filter(pk=version.pk).update(company=foreign)
        with self.assertRaises(PermissionDenied):DeletionGraph(self.workspace,self.admin)
        self.assertTrue(PlanningWorkspace.objects.exists())

    def test_late_failure_rolls_back_internal_links_and_all_records(self):
        revision=self.revision();token=self.client.get(self.url).context['preview_token']
        from .deletion import delete_workspace
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):delete_workspace(self.workspace,self.admin,confirm_history=True,preview_token=token)
        self.workspace.refresh_from_db();self.assertEqual(self.workspace.baseline_version_id,self.base.pk)
        revision.refresh_from_db();self.assertEqual(revision.previous_version_id,self.base.pk)
        self.assertTrue(DailyFact.objects.exists())
