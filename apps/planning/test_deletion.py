from datetime import date
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork
from apps.production.models import DailyFact
from .models import PlanningWorkspace, GlobalPlanVersion, WorkMonthAllocation


class PlanningDeletionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Plan deletion')
        cls.user=User.objects.create_user(username='delete-planner',company=cls.company,role=Role.objects.get(code='PLANNER'))
        project=Project.objects.create(company=cls.company,code='p',name='p')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=project,code='o',name='o')
        section=Section.objects.create(company=cls.company,construction_object=cls.obj,code='s',name='s')
        cls.work=ProjectWork.objects.create(company=cls.company,section=section,code='w',name='Work',unit='м')
        cls.workspace=PlanningWorkspace.objects.create(company=cls.company,construction_object=cls.obj,name='Draft period',start_date=date(2026,10,1),end_date=date(2026,10,31))
        cls.base=GlobalPlanVersion.objects.create(company=cls.company,construction_object=cls.obj,workspace=cls.workspace,version_kind='BASELINE',version_number=1,start_date=cls.workspace.start_date,end_date=cls.workspace.end_date)
        cls.workspace.baseline_version=cls.base;cls.workspace.save()
        WorkMonthAllocation.objects.create(company=cls.company,version=cls.base,work=cls.work,month=date(2026,10,1),quantity=10)
        cls.fact=DailyFact.objects.create(company=cls.company,project_work=cls.work,date=date(2026,10,6),actual_quantity=2)

    def setUp(self):
        self.client.force_login(self.user)

    def test_confirmation_get_is_read_only_and_post_deletes_draft_plan_not_facts(self):
        url=reverse('planning:workspace_delete',args=[self.workspace.pk])
        self.assertContains(self.client.get(url),'Подтвердить удаление')
        self.assertTrue(PlanningWorkspace.objects.exists())
        self.assertContains(self.client.get(reverse('planning:workspace_list')),url)
        self.assertEqual(self.client.post(url).status_code,302)
        self.assertFalse(PlanningWorkspace.objects.exists())
        self.assertFalse(GlobalPlanVersion.objects.exists())
        self.assertFalse(WorkMonthAllocation.objects.exists())
        self.assertTrue(ProjectWork.objects.filter(pk=self.work.pk).exists())
        self.assertTrue(DailyFact.objects.filter(pk=self.fact.pk).exists())
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists())

    def test_locked_statuses_block_deletion_and_hide_button(self):
        for status in ('SUBMITTED','APPROVED','COMPLETED'):
            GlobalPlanVersion.objects.filter(pk=self.base.pk).update(status=status)
            url=reverse('planning:workspace_delete',args=[self.workspace.pk])
            self.assertNotContains(self.client.get(reverse('planning:workspace_list')), 'href="'+url+'"')
            self.assertEqual(self.client.post(url).status_code,400)
            self.assertTrue(PlanningWorkspace.objects.filter(pk=self.workspace.pk).exists())
            self.assertTrue(GlobalPlanVersion.objects.filter(pk=self.base.pk).exists())

    def test_baseline_cannot_be_deleted_separately(self):
        self.assertEqual(self.client.post(reverse('planning:global_delete',args=[self.base.pk])).status_code,400)
        self.assertTrue(GlobalPlanVersion.objects.filter(pk=self.base.pk).exists())

    def test_legacy_draft_can_be_deleted_and_approved_cannot(self):
        version=GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,version_number=2,start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        url=reverse('planning:global_delete',args=[version.pk])
        self.assertEqual(self.client.post(url).status_code,302)
        self.assertTrue(GlobalPlanVersion.objects.filter(pk=self.base.pk).exists())
        GlobalPlanVersion.objects.filter(pk=self.base.pk).update(status='APPROVED')
        self.assertEqual(self.client.post(reverse('planning:global_delete',args=[self.base.pk])).status_code,400)

    def test_workspace_history_offers_draft_deletion_with_completed_baseline(self):
        GlobalPlanVersion.objects.filter(pk=self.base.pk).update(status='COMPLETED')
        version = GlobalPlanVersion.objects.create(
            company=self.company, construction_object=self.obj, workspace=self.workspace,
            version_kind='FORECAST', version_number=2,
            start_date=self.workspace.start_date, end_date=self.workspace.end_date,
            previous_version=self.base,
        )
        response = self.client.get(reverse('planning:workspace_detail', args=[self.workspace.pk]))
        self.assertContains(response, reverse('planning:global_delete', args=[version.pk]))
        self.assertNotContains(response, 'href="' + reverse('planning:global_delete', args=[self.base.pk]) + '"')
        self.assertContains(response, 'Удаление недоступно')
        self.assertEqual(self.client.post(reverse('planning:global_delete', args=[version.pk])).status_code, 302)
        self.assertTrue(GlobalPlanVersion.objects.filter(pk=self.base.pk).exists())

    def test_protected_external_revision_rolls_back_entire_workspace_delete(self):
        GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,version_number=2,start_date=self.workspace.start_date,end_date=self.workspace.end_date,previous_version=self.base)
        response=self.client.post(reverse('planning:workspace_delete',args=[self.workspace.pk]))
        self.assertEqual(response.status_code,400)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.baseline_version_id,self.base.pk)
        self.assertTrue(WorkMonthAllocation.objects.filter(version=self.base).exists())

    def test_foreign_company_and_non_editing_roles_cannot_delete(self):
        other=Company.objects.create(name='Foreign deletion')
        user=User.objects.create_user(username='foreign-delete',company=other,role=Role.objects.get(code='PLANNER'))
        self.client.force_login(user)
        for name,pk in [('planning:workspace_delete',self.workspace.pk),('planning:global_delete',self.base.pk)]:
            self.assertEqual(self.client.get(reverse(name,args=[pk])).status_code,404)
            self.assertEqual(self.client.post(reverse(name,args=[pk])).status_code,404)
        for code in ('MANAGER','FOREMAN'):
            user=User.objects.create_user(username='no-delete-'+code,company=self.company,role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('planning:workspace_delete',args=[self.workspace.pk])).status_code,403)
        self.assertTrue(PlanningWorkspace.objects.filter(pk=self.workspace.pk).exists())
