from datetime import date
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User,Role,Company
from apps.projects.models import Project,ConstructionObject,Section
from apps.production.models import DailyFact,FuelFact
from apps.planning import test_deletion as fixtures
from apps.planning.models import PlanningWorkspace,GlobalPlanVersion,DailySchedule,DailyScheduleEntry,MonthlyPlan,PlanVersion
from .models import ProjectWork

class WorkPurgeTests(TestCase):
    setUpTestData=classmethod(fixtures.PlanningDeletionTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.user)
        self.url=reverse('works:work_purge')
    def confirm(self,response=None):
        response=response or self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        return self.client.post(self.url,{'confirm':'1','confirm_related':'on','selection':response.context['selection']})

    def test_purge_all_removes_work_and_related_plans_and_fact(self):
        unrelated=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=date(2026,11,1),revision=1,created_by=self.user)
        linked=DailySchedule.objects.create(company=self.company,construction_object=self.obj,source=self.base,month=date(2026,10,1),revision=1,created_by=self.user)
        FuelFact.objects.create(company=self.company,construction_object=self.obj,date=date(2026,10,6),fuel_type='DIESEL',opening_balance=30,actual_liters=10,actual_balance=20)
        preview=self.client.get(self.url)
        self.assertContains(preview,'Факт работ')
        self.assertTrue(ProjectWork.objects.exists())
        self.assertEqual(self.confirm(preview).status_code,302)
        self.assertFalse(ProjectWork.objects.exists());self.assertFalse(DailyFact.objects.exists())
        self.assertFalse(PlanningWorkspace.objects.exists());self.assertFalse(GlobalPlanVersion.objects.exists())
        self.assertEqual(list(DailySchedule.objects.values_list('pk',flat=True)),[unrelated.pk])
        self.assertTrue(ConstructionObject.objects.filter(pk=self.obj.pk).exists());self.assertTrue(FuelFact.objects.exists())

    def test_selected_bulk_deletion_uses_purge_and_retains_other_work(self):
        other=ProjectWork.objects.create(company=self.company,section=self.work.section,name='Оставить',unit='шт')
        response=self.client.post(reverse('works:bulk_delete',args=['works']),{'selected':[str(self.work.pk)]})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Удалить работы и связанные данные')
        self.assertEqual(self.confirm(response).status_code,302)
        self.assertEqual(list(ProjectWork.objects.values_list('pk',flat=True)),[other.pk])

    def test_new_work_after_preview_requires_updated_confirmation(self):
        preview=self.client.get(self.url)
        ProjectWork.objects.create(company=self.company,section=self.work.section,name='Новая работа',unit='м')
        response=self.confirm(preview)
        self.assertEqual(response.status_code,400)
        self.assertEqual(ProjectWork.objects.count(),2)
        self.assertTrue(DailyFact.objects.exists())

    def test_planner_cannot_delete_work_or_plans_by_direct_requests(self):
        planner=User.objects.create_user(username='purge-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        planner.assigned_objects.add(self.obj);self.client.force_login(planner)
        for url in [self.url,reverse('works:work_delete',args=[self.work.pk]),reverse('planning:workspace_delete',args=[self.workspace.pk]),reverse('planning:global_delete',args=[self.base.pk])]:
            self.assertEqual(self.client.get(url).status_code,403)
            self.assertEqual(self.client.post(url).status_code,403)
        self.assertEqual(self.client.post(reverse('works:bulk_delete',args=['works']),{'selected':[self.work.pk]}).status_code,403)
        self.assertNotContains(self.client.get(reverse('works:work_list')),'Удалить все работы компании')
        self.assertNotContains(self.client.get(reverse('works:work_list')),'data-bulk-submit')
        self.assertTrue(ProjectWork.objects.exists())

    def test_other_company_work_is_not_in_purge_and_cannot_be_selected(self):
        company=Company.objects.create(name='Other purge company')
        project=Project.objects.create(company=company,name='Other')
        obj=ConstructionObject.objects.create(company=company,project=project,name='Other')
        section=Section.objects.create(company=company,construction_object=obj,name='Other')
        work=ProjectWork.objects.create(company=company,section=section,name='Other',unit='м')
        self.assertEqual(self.client.post(reverse('works:bulk_delete',args=['works']),{'selected':[work.pk]}).status_code,403)
        self.assertEqual(self.confirm().status_code,302)
        self.assertTrue(ProjectWork.objects.filter(pk=work.pk).exists())

    def test_monthly_history_and_json_only_plan_references_are_removed(self):
        monthly=MonthlyPlan.objects.create(company=self.company,project_work=self.work,year=2026,month=10,planned_quantity=10,start_date=self.workspace.start_date,end_date=self.workspace.end_date)
        source=PlanVersion.objects.create(company=self.company,monthly_plan=monthly,version_number=1,status='APPROVED',created_by=self.user)
        other=ProjectWork.objects.create(company=self.company,section=self.work.section,name='Другой каталог',unit='м')
        self.base.work_allocations.all().delete()
        GlobalPlanVersion.objects.filter(pk=self.base.pk).update(snapshot={'works':[{'id':other.pk,'kind':'SIMPLE','unit':'м','versions':[{'id':source.pk}]}]})
        response=self.client.post(reverse('works:bulk_delete',args=['works']),{'selected':[self.work.pk]})
        self.assertEqual(self.confirm(response).status_code,302)
        self.assertFalse(MonthlyPlan.objects.exists());self.assertFalse(PlanVersion.objects.exists());self.assertFalse(GlobalPlanVersion.objects.exists())
        self.assertTrue(ProjectWork.objects.filter(pk=other.pk).exists())

    def test_named_activity_is_removed_and_remaining_staff_is_reprojected(self):
        from apps.resources.models import Brigade,Employee
        from apps.resources.position_catalog import position_for
        from apps.production.models import EmployeeActivity,LaborFact
        from apps.production.dispatch import project_resources
        position=position_for(Brigade.objects.create(company=self.company,name='Монтажник'))
        first=Employee.objects.create(company=self.company,last_name='Работник',first_name='Первый',position=position)
        second=Employee.objects.create(company=self.company,last_name='Работник',first_name='Второй',position=position)
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.fact.date,employee=first,position=position,work=self.work,activity='Монтаж')
        remaining=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.fact.date,employee=second,position=position,general=True,activity='Общие задачи объекта')
        project_resources(self.obj,self.fact.date,'labor')
        self.assertEqual(LaborFact.objects.get().actual_workers,2)
        self.assertEqual(self.confirm().status_code,302)
        self.assertEqual(list(EmployeeActivity.objects.values_list('pk',flat=True)),[remaining.pk])
        self.assertEqual(Employee.objects.count(),2)
        self.assertEqual(LaborFact.objects.get().actual_workers,1)

    def test_deletion_requires_explicit_fact_confirmation(self):
        preview=self.client.get(self.url)
        response=self.client.post(self.url,{'confirm':'1','selection':preview.context['selection']})
        self.assertEqual(response.status_code,400)
        self.assertTrue(ProjectWork.objects.exists());self.assertTrue(DailyFact.objects.exists())

    def test_late_failure_rolls_back_plan_work_and_fact_deletion(self):
        preview=self.client.get(self.url)
        from apps.works.purge import WorkPurge
        original=WorkPurge.perform
        def failure(instance):
            original(instance)
            raise RuntimeError('late failure')
        with patch.object(WorkPurge,'perform',failure):
            with self.assertRaises(RuntimeError):self.confirm(preview)
        self.assertTrue(ProjectWork.objects.exists());self.assertTrue(DailyFact.objects.exists())
        self.assertTrue(PlanningWorkspace.objects.exists());self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.baseline_version_id,self.base.pk)
