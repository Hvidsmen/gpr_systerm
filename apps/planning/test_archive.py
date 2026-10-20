from datetime import date
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import PermissionDenied
from apps.accounts.models import User,Role,Company
from apps.projects.models import Project,ConstructionObject,Section
from apps.works.models import ProjectWork
from apps.production.models import DailyFact
from .models import PlanningWorkspace,GlobalPlanVersion,DailySchedule,WorkTask
from .archive import set_archived
from .report_services import select_sources
from .workspace_services import current_workspace_version

JAN=date(2026,1,1)


class ArchiveTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Archive company')
        project=Project.objects.create(company=self.company,name='Project')
        self.obj=ConstructionObject.objects.create(company=self.company,project=project,name='Object')
        self.admin=User.objects.create_user(username='archive-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.planner=User.objects.create_user(username='archive-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.planner.assigned_objects.add(self.obj)
        self.workspace=PlanningWorkspace.objects.create(company=self.company,construction_object=self.obj,name='Archive this GPR',start_date=JAN,end_date=date(2026,12,31))
        self.version=GlobalPlanVersion.objects.create(company=self.company,workspace=self.workspace,construction_object=self.obj,start_date=JAN,end_date=date(2026,12,31),version_number=1,version_kind='BASELINE',status='APPROVED',snapshot={'works':[],'resources':{}})
        self.workspace.baseline_version=self.version;self.workspace.save()
        self.schedule=DailySchedule.objects.create(company=self.company,source=self.version,month=JAN,revision=1,status='APPLIED',created_by=self.admin)
        section=Section.objects.create(company=self.company,construction_object=self.obj,name='Section')
        self.work=ProjectWork.objects.create(company=self.company,section=section,name='Work',unit='m')
        self.fact=DailyFact.objects.create(company=self.company,project_work=self.work,date=JAN,actual_quantity=2)
        self.client.force_login(self.admin)
        self.url=reverse('planning:workspace_archive',args=[self.workspace.pk])

    def test_read_only_preview_confirmation_and_restore(self):
        self.assertContains(self.client.get(self.url),'Поместить план в архив')
        self.workspace.refresh_from_db();self.assertFalse(self.workspace.is_archived)
        self.client.post(self.url,{'action':'archive'})
        self.workspace.refresh_from_db();self.assertFalse(self.workspace.is_archived)
        response=self.client.post(self.url,{'action':'archive','confirm':'on'})
        self.assertEqual(response.status_code,302)
        self.workspace.refresh_from_db();self.assertTrue(self.workspace.is_archived)
        self.assertEqual(self.workspace.archived_by,self.admin)
        self.assertNotContains(self.client.get(reverse('planning:workspace_list')),'Archive this GPR')
        self.assertContains(self.client.get(reverse('planning:workspace_archive_list')),'Archive this GPR')
        self.assertEqual(self.client.get(reverse('planning:workspace_detail',args=[self.workspace.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('planning:workspace_forecast',args=[self.workspace.pk])).status_code,404)
        self.assertTrue(GlobalPlanVersion.objects.filter(pk=self.version.pk).exists())
        self.assertTrue(DailyFact.objects.filter(pk=self.fact.pk).exists())
        self.assertTrue(ProjectWork.objects.filter(pk=self.work.pk).exists())
        self.assertTrue(DailySchedule.objects.filter(pk=self.schedule.pk).exists())
        self.client.post(self.url,{'action':'restore','confirm':'on'})
        self.assertContains(self.client.get(reverse('planning:workspace_list')),'Archive this GPR')

    def test_not_selected_as_current_not_in_filters_tasks_or_smg(self):
        from .report_forms import MatrixReportForm
        from .task_services import visible
        task=WorkTask.objects.create(company=self.company,key='archive-task',kind='MANUAL',title='Archive-related task',assigned_to=self.planner,construction_object=self.obj,version=self.version)
        self.assertIsNotNone(current_workspace_version(self.company,self.obj,JAN))
        set_archived(self.admin,self.workspace.pk,True)
        self.assertIsNone(current_workspace_version(self.company,self.obj,JAN))
        sources=select_sources(self.admin,{'mode':'latest','start':JAN,'end':date(2026,1,31)},[self.obj])
        self.assertFalse(sources.get(self.obj.pk))
        form=MatrixReportForm(user=self.admin)
        for name in form.version_fields:self.assertNotIn(self.version.pk,list(form.fields[name].queryset.values_list('pk',flat=True)))
        self.assertFalse(visible(self.planner).filter(pk=task.pk).exists())
        self.assertEqual(self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('planning:daily_schedule_apply',args=[self.schedule.pk])).status_code,404)

    def test_same_company_assigned_objects_and_history_permissions(self):
        with self.assertRaises(PermissionDenied):set_archived(self.planner,self.workspace.pk,True)
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='DRAFT')
        set_archived(self.planner,self.workspace.pk,True)
        set_archived(self.planner,self.workspace.pk,False)
        self.planner.assigned_objects.clear();self.client.force_login(self.planner)
        self.assertEqual(self.client.get(self.url).status_code,404)
        other=Company.objects.create(name='Foreign archive')
        foreign=User.objects.create_user(username='foreign-archive',company=other,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(foreign);self.assertEqual(self.client.post(self.url,{'confirm':'on','action':'archive'}).status_code,404)

    def test_deletion_block_has_archive_option_and_preserves_links(self):
        response=self.client.get(reverse('planning:workspace_delete',args=[self.workspace.pk]))
        self.assertContains(response,self.url)
        other=GlobalPlanVersion.objects.create(company=self.company,construction_object=self.obj,start_date=JAN,end_date=date(2026,12,31),version_number=2,previous_version=self.version)
        set_archived(self.admin,self.workspace.pk,True)
        other.refresh_from_db();self.assertEqual(other.previous_version_id,self.version.pk)


    def test_stale_instances_cannot_start_versions_or_smg(self):
        from .workspace_services import WorkspaceService
        from .global_services import GlobalPlanService
        from .daily_schedule import validate_source
        from django.core.exceptions import ValidationError
        set_archived(self.admin,self.workspace.pk,True)
        with self.assertRaises(PermissionDenied):WorkspaceService.new_version(self.workspace,self.admin,'BASELINE')
        with self.assertRaises(PermissionDenied):GlobalPlanService.transition(self.version,self.admin,'submit')
        with self.assertRaises(ValidationError):validate_source(self.version,JAN)
