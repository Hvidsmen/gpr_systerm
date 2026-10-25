from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,User,Role
from apps.projects.models import ConstructionObject,Project
from . import test_workspace as fixtures
from .models import ObjectWorkPlan,ObjectWorkPlanRow,WorkMonthAllocation,WorkResourceAssignment
from .workspace_services import WorkspaceService
from .object_plans import add_branch,ObjectPlanForm
from .independent_smg import create


class ObjectPlanTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.planner)
        self.plan=ObjectWorkPlan.objects.create(company=self.company,construction_object=self.obj,created_by=self.planner,name='Общий состав объекта')
        self.url=reverse('planning:object_plan_detail',args=[self.plan.pk])

    def test_third_tab_and_independent_creation(self):
        response=self.client.get(reverse('planning:workspace_list'))
        self.assertContains(response,reverse('planning:object_plan_list'))
        response=self.client.post(reverse('planning:object_plan_create'),{'construction_object':self.obj.pk,'name':'Независимый план','description':'Работы целиком'})
        self.assertEqual(response.status_code,302,response.content)
        plan=ObjectWorkPlan.objects.get(name='Независимый план')
        self.assertFalse(plan.gpr_versions.exists());self.assertFalse(plan.smg_schedules.exists())
        self.assertContains(self.client.get(response.url),'Добавить работы')

    def test_add_complex_work_norms_and_repeat_keeps_volumes(self):
        count=add_branch(self.planner,self.plan,f'package:{self.composite.pk}',Decimal(2))
        self.assertEqual(count,3)
        self.assertEqual(self.plan.rows.get(work_item=self.a).quantity,self.a.quantity_per_unit*2)
        self.assertEqual(self.plan.rows.get(work_item=self.b).quantity,self.b.quantity_per_unit*2)
        self.assertEqual(add_branch(self.planner,self.plan,f'package:{self.composite.pk}',Decimal(8)),0)
        self.assertEqual(self.plan.rows.get(work_item__isnull=True).quantity,2)
        self.assertFalse(WorkMonthAllocation.objects.exists());self.assertFalse(WorkResourceAssignment.objects.exists())

    def test_single_item_does_not_add_siblings(self):
        self.assertEqual(add_branch(self.planner,self.plan,f'item:{self.a.pk}',Decimal(7)),1)
        self.assertEqual(self.plan.rows.get().quantity,7)
        self.assertFalse(self.plan.rows.filter(work_item=self.b).exists())

    def test_rows_edit_is_independent_from_linked_gpr_and_smg(self):
        version=WorkspaceService.create(self.planner,self.obj,'Период',fixtures.JAN,date(2026,1,31)).baseline_version
        allocation=WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.simple,month=fixtures.JAN,quantity=11)
        schedule=create(self.planner,self.obj,fixtures.JAN)
        response=self.client.post(self.url,{'action':'settings','construction_object':self.obj.pk,'name':self.plan.name,'description':'','gpr_versions':[version.pk],'smg_schedules':[schedule.pk]})
        self.assertEqual(response.status_code,302,response.content)
        add_branch(self.planner,self.plan,f'package:{self.simple.pk}',Decimal(5))
        row=self.plan.rows.get()
        response=self.client.post(self.url,{'action':'rows','rows-TOTAL_FORMS':'1','rows-INITIAL_FORMS':'1','rows-0-id':row.pk,'rows-0-quantity':'22'})
        self.assertEqual(response.status_code,302,response.content)
        row.refresh_from_db();allocation.refresh_from_db()
        self.assertEqual(row.quantity,22);self.assertEqual(allocation.quantity,11)
        self.assertFalse(schedule.entries.exists())
        self.assertContains(self.client.get(reverse('planning:global_detail',args=[version.pk])),self.plan.name)
        self.assertContains(self.client.get(reverse('planning:daily_schedule_detail',args=[schedule.pk])),self.plan.name)
        self.assertEqual(list(self.plan.gpr_versions.all()),[version])

    def test_cross_object_link_rejected(self):
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой объект')
        # Create through the service as admin because this planner is assigned only to the first object.
        admin=User.objects.create_user(username='object-plan-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        version=WorkspaceService.create(admin,other,'Чужой объект',fixtures.JAN,date(2026,1,31)).baseline_version
        self.client.force_login(admin)
        response=self.client.post(self.url,{'action':'settings','name':self.plan.name,'gpr_versions':[version.pk]})
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.plan.gpr_versions.exists())

    def test_company_and_assigned_object_scope(self):
        company=Company.objects.create(name='Другая компания')
        project=Project.objects.create(company=company,name='Другой проект')
        obj=ConstructionObject.objects.create(company=company,project=project,name='Скрытый объект')
        foreign=ObjectWorkPlan.objects.create(company=company,construction_object=obj,name='Скрытый план',created_by=self.planner)
        self.assertEqual(self.client.get(reverse('planning:object_plan_detail',args=[foreign.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('planning:object_plan_list')),'Скрытый план')
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Не назначен')
        unassigned=ObjectWorkPlan.objects.create(company=self.company,construction_object=other,name='Не назначенный план',created_by=self.planner)
        self.assertEqual(self.client.get(reverse('planning:object_plan_detail',args=[unassigned.pk])).status_code,404)
        observer=User.objects.create_user(username='object-plan-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        self.assertEqual(self.client.get(self.url).status_code,200)
        self.assertEqual(self.client.post(self.url,{'action':'add'}).status_code,403)

    def test_foreign_row_id_cannot_be_changed(self):
        another=ObjectWorkPlan.objects.create(company=self.company,construction_object=self.obj,name='Другой план',created_by=self.planner)
        row=ObjectWorkPlanRow.objects.create(company=self.company,plan=another,work=self.simple,quantity=7)
        response=self.client.post(self.url,{'action':'rows','rows-TOTAL_FORMS':'1','rows-INITIAL_FORMS':'1','rows-0-id':row.pk,'rows-0-quantity':'99'})
        self.assertEqual(response.status_code,400)
        row.refresh_from_db();self.assertEqual(row.quantity,7)

    def test_admin_work_purge_previews_object_plan_rows(self):
        from apps.works.purge import WorkPurge
        admin=User.objects.create_user(username='purge-object-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        add_branch(self.planner,self.plan,f'package:{self.simple.pk}',Decimal(1))
        purge=WorkPurge(admin,[self.simple.pk])
        self.assertTrue(any(row['label']=='Работы в планах на весь объект' and row['count']==1 for row in purge.counts))
        from django.db import transaction
        with transaction.atomic():purge.perform()
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.rows.exists())

    def test_negative_volume_does_not_change_rows(self):
        add_branch(self.planner,self.plan,f'package:{self.simple.pk}',Decimal(5))
        row=self.plan.rows.get()
        response=self.client.post(self.url,{'action':'rows','rows-TOTAL_FORMS':'1','rows-INITIAL_FORMS':'1','rows-0-id':row.pk,'rows-0-quantity':'-1'})
        self.assertEqual(response.status_code,400)
        row.refresh_from_db();self.assertEqual(row.quantity,5)

    def test_deleting_linked_gpr_preserves_independent_plan(self):
        from .deletion_graph import DeletionGraph
        from django.db import transaction
        version=WorkspaceService.create(self.planner,self.obj,'Связанный период',fixtures.JAN,date(2026,1,31)).baseline_version
        self.plan.gpr_versions.add(version)
        add_branch(self.planner,self.plan,f'package:{self.simple.pk}',Decimal(5))
        admin=User.objects.create_user(username='delete-linked-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        with transaction.atomic():DeletionGraph(version.workspace,admin).delete()
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.rows.get().quantity,5)
        self.assertFalse(self.plan.gpr_versions.exists())
