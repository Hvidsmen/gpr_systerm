from datetime import date
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from apps.accounts.models import User,Role,Company
from apps.projects.models import Project,ConstructionObject
from apps.planning.models import PlanningWorkspace,GlobalPlanVersion,DailySchedule
from apps.planning.workspace_services import WorkspaceService
from apps.planning.report_services import selected_objects
from apps.planning.workspace_forms import WorkspaceForm
from core.permissions import scope_queryset


class PlannerScopeTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Planner scope')
        project=Project.objects.create(company=self.company,name='Project')
        self.allowed=ConstructionObject.objects.create(company=self.company,project=project,name='Allowed')
        self.denied=ConstructionObject.objects.create(company=self.company,project=project,name='Denied')
        self.admin=User.objects.create_user(username='scope-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.planner=User.objects.create_user(username='scope-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.planner.assigned_objects.add(self.allowed)
        self.a=WorkspaceService.create(self.admin,self.allowed,'Allowed plan',date(2026,1,1),date(2026,12,31))
        self.b=WorkspaceService.create(self.admin,self.denied,'Denied plan',date(2026,1,1),date(2026,12,31))
        self.client.force_login(self.planner)

    def test_lists_forms_reports_and_admin(self):
        response=self.client.get(reverse('planning:workspace_list'))
        self.assertContains(response,'Allowed plan');self.assertNotContains(response,'Denied plan')
        self.assertEqual(self.client.get(reverse('planning:workspace_detail',args=[self.a.pk])).status_code,200)
        self.assertEqual(list(WorkspaceForm(user=self.planner).fields['construction_object'].queryset),[self.allowed])
        objects=selected_objects(self.planner,{'start':date(2026,1,1),'end':date(2026,12,31)})
        self.assertEqual(objects,[self.allowed])
        self.assertEqual(scope_queryset(ConstructionObject.objects.all(),self.admin).count(),2)

    def test_direct_gpr_and_import_and_edit_are_blocked(self):
        for name,pk in [('workspace_detail',self.b.pk),('workspace_edit',self.b.baseline_version_id),('global_detail',self.b.baseline_version_id),('workspace_gpr_import',self.b.baseline_version_id),('workspace_excel_import',self.b.baseline_version_id),('workspace_forecast',self.b.pk),('daily_schedule_create',self.b.baseline_version_id)]:
            url=reverse('planning:'+name,args=[pk])
            self.assertEqual(self.client.get(url).status_code,404,name)
            self.assertEqual(self.client.post(url,{}).status_code,404,name)
        with self.assertRaises(PermissionDenied):WorkspaceService.create(self.planner,self.denied,'Forbidden',date(2026,1,1),date(2026,12,31))

    def test_smg_and_no_assignments(self):
        schedule=DailySchedule.objects.create(company=self.company,source=self.b.baseline_version,month=date(2026,11,1),revision=1,created_by=self.admin)
        self.assertEqual(self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('planning:daily_schedule_apply',args=[schedule.pk])).status_code,404)
        self.planner.assigned_objects.clear()
        self.assertFalse(scope_queryset(PlanningWorkspace.objects.all(),self.planner).exists())
        self.assertEqual(self.client.get(reverse('planning:workspace_detail',args=[self.a.pk])).status_code,404)


    def test_overview_uses_target_assignments_when_admin_impersonates(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('accounts:impersonation_start',args=[self.planner.pk])).status_code,302)
        response=self.client.get(reverse('dashboard:index'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(list(response.context['form'].fields['objects'].queryset),[self.allowed])
        self.assertNotContains(response,'Denied')
        response=self.client.get(reverse('planning:report_matrix'),{'objects':self.denied.pk,'start':'2026-01-01','end':'2026-12-31'})
        self.assertTrue(response.context['form'].errors)
        self.assertNotContains(response,'Denied plan')
