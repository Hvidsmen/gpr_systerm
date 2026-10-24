from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
from apps.accounts.models import User,Role,Company
from apps.production import test_dispatch as fixtures
from apps.production.models import EmployeeActivity,EquipmentActivity,LaborFact,EquipmentFact,FuelFact
from .models import Brigade,Position,EquipmentType,Equipment,Employee

class ResourcePurgeTests(TestCase):
    setUpTestData=classmethod(fixtures.DispatcherTests.setUpTestData.__func__)
    def setUp(self):self.client.force_login(self.users['ADMIN'])
    def endpoint(self,kind):return reverse('resources:catalogue_purge',args=[kind])
    def confirm(self,kind,response=None):
        response=response or self.client.get(self.endpoint(kind))
        self.assertEqual(response.status_code,200)
        return self.client.post(self.endpoint(kind),{'confirm':'1','confirm_related':'on','selection':response.context['selection']})

    def test_positions_are_purged_but_employee_is_retained(self):
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,general=True,activity='Общие задачи')
        LaborFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,brigade=self.brigade,actual_workers=1)
        response=self.client.get(self.endpoint('brigades'));self.assertContains(response,'Сотрудники сохраняются')
        self.assertEqual(self.confirm('brigades',response).status_code,302)
        self.assertFalse(Brigade.objects.filter(company=self.company).exists());self.assertFalse(Position.objects.filter(company=self.company).exists())
        self.employee.refresh_from_db();self.assertIsNone(self.employee.position_id)
        self.assertTrue(Employee.objects.filter(pk=self.employee.pk).exists())
        self.assertFalse(EmployeeActivity.objects.exists());self.assertFalse(LaborFact.objects.exists())
        self.assertTrue(EquipmentType.objects.filter(pk=self.type.pk).exists())

    def test_equipment_instances_and_related_activity_are_purged(self):
        EquipmentActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment=self.machine,state='REPAIR',activity='Ремонт')
        EquipmentFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment_type=self.type,equipment_number=self.machine.plate_number,actual_count=0)
        FuelFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,fuel_type='DIESEL',opening_balance=100,actual_liters=20,actual_balance=80)
        self.assertEqual(self.confirm('equipment').status_code,302)
        self.assertFalse(EquipmentType.objects.filter(company=self.company).exists());self.assertFalse(Equipment.objects.exists())
        self.assertFalse(EquipmentActivity.objects.exists());self.assertFalse(EquipmentFact.objects.exists())
        self.assertTrue(FuelFact.objects.exists());self.assertTrue(Employee.objects.exists());self.assertTrue(Brigade.objects.exists())

    def test_selected_type_keeps_unrelated_type_and_other_company(self):
        other=EquipmentType.objects.create(company=self.company,name='Оставить')
        company=Company.objects.create(name='Другой справочник')
        foreign=EquipmentType.objects.create(company=company,name='Чужая техника')
        response=self.client.post(reverse('resources:bulk_delete',args=['equipment']),{'selected':[self.type.pk]})
        self.assertEqual(self.confirm('equipment',response).status_code,302)
        self.assertTrue(EquipmentType.objects.filter(pk=other.pk).exists());self.assertTrue(EquipmentType.objects.filter(pk=foreign.pk).exists())

    def test_single_delete_uses_the_same_reviewed_cascade(self):
        response=self.client.get(reverse('resources:equipment_type_delete',args=[self.type.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['preview'].requested,[self.type.pk])
        self.assertTrue(EquipmentType.objects.filter(pk=self.type.pk).exists())
        self.assertEqual(self.confirm('equipment',response).status_code,302)
        self.assertFalse(EquipmentType.objects.filter(pk=self.type.pk).exists())

    def test_new_record_requires_updated_preview(self):
        response=self.client.get(self.endpoint('equipment'))
        EquipmentType.objects.create(company=self.company,name='После предпросмотра')
        self.assertEqual(self.confirm('equipment',response).status_code,400)
        self.assertTrue(EquipmentType.objects.filter(pk=self.type.pk).exists())

    def test_only_administrator_can_delete_resources(self):
        planner=User.objects.create_user(username='resource-purge-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.client.force_login(planner)
        for kind in ['brigades','equipment']:
            self.assertEqual(self.client.get(self.endpoint(kind)).status_code,403)
            self.assertEqual(self.client.post(self.endpoint(kind)).status_code,403)
            self.assertEqual(self.client.post(reverse('resources:bulk_delete',args=[kind]),{'selected':[self.brigade.pk]}).status_code,403)
        self.assertNotContains(self.client.get(reverse('resources:brigade_list')),'data-bulk-submit')
        self.assertNotContains(self.client.get(reverse('resources:equipment_type_list')),'Удалить всю технику компании')

    def test_resource_plan_dependencies_are_included(self):
        from datetime import date
        from apps.planning.workspace_services import WorkspaceService
        from apps.planning.models import ResourceMonthAllocation,GlobalPlanVersion
        for kind in ['brigades','equipment']:
            workspace=WorkspaceService.create(self.users['ADMIN'],self.obj,'Ресурсный план',self.day.replace(day=1),date(2026,10,31))
            field={'brigade':self.brigade} if kind=='brigades' else {'equipment_type':self.type}
            ResourceMonthAllocation.objects.create(company=self.company,version=workspace.baseline_version,month=self.day.replace(day=1),kind='labor' if kind=='brigades' else 'equipment',count=1,**field)
            response=self.client.get(self.endpoint(kind))
            self.assertTrue(response.context['preview'].graph.groups)
            self.assertEqual(self.confirm(kind,response).status_code,302)
            self.assertFalse(GlobalPlanVersion.objects.exists())

    def test_position_in_equipment_crew_snapshot_removes_its_plan(self):
        from datetime import date
        from apps.planning.workspace_services import WorkspaceService
        from apps.planning.models import WorkResourceAssignment,GlobalPlanVersion
        workspace=WorkspaceService.create(self.users['ADMIN'],self.obj,'Экипаж',self.day.replace(day=1),date(2026,10,31))
        WorkResourceAssignment.objects.create(company=self.company,version=workspace.baseline_version,equipment_type=self.type,start_date=workspace.start_date,end_date=workspace.end_date,crew_snapshot=[{'position_id':self.employee.position_id,'count':1}])
        self.assertEqual(self.confirm('brigades').status_code,302)
        self.assertFalse(GlobalPlanVersion.objects.exists());self.assertFalse(WorkResourceAssignment.objects.exists())
        self.assertTrue(EquipmentType.objects.exists())

    def test_unrelated_legacy_totals_survive_equipment_purge(self):
        other_type=EquipmentType.objects.create(company=self.company,name='Другой вид техники')
        other_brigade=Brigade.objects.create(company=self.company,name='Другая должность')
        EquipmentFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment_type=other_type,equipment_number='OTHER',actual_count=5)
        LaborFact.objects.create(company=self.company,construction_object=self.obj,date=self.day,brigade=other_brigade,actual_workers=7)
        EquipmentActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,equipment=self.machine,state='REPAIR',activity='Ремонт')
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,equipment=self.machine,general=True,activity='Ремонт')
        from datetime import timedelta
        from .position_catalog import position_for
        other_day=self.day+timedelta(days=1)
        second=Employee.objects.create(company=self.company,last_name='Другой',first_name='Сотрудник',position=position_for(other_brigade))
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=other_day,employee=second,position=second.position,equipment=self.machine,general=True,activity='Ремонт')
        LaborFact.objects.create(company=self.company,construction_object=self.obj,date=other_day,brigade=self.brigade,actual_workers=9)
        response=self.client.post(reverse('resources:bulk_delete',args=['equipment']),{'selected':[self.type.pk]})
        self.assertEqual(self.confirm('equipment',response).status_code,302)
        self.assertEqual(EquipmentFact.objects.get(equipment_type=other_type).actual_count,5)
        self.assertEqual(LaborFact.objects.get(brigade=other_brigade,date=self.day).actual_workers,7)
        self.assertEqual(LaborFact.objects.get(brigade=self.brigade,date=self.day).actual_workers,0)
        self.assertEqual(LaborFact.objects.get(brigade=self.brigade,date=other_day).actual_workers,9)

    def test_failure_rolls_back_catalogue_and_activity(self):
        from .purge import ResourcePurge
        response=self.client.get(self.endpoint('equipment'));original=ResourcePurge.perform
        def fail(instance):original(instance);raise RuntimeError('late failure')
        with patch.object(ResourcePurge,'perform',fail):
            with self.assertRaises(RuntimeError):self.confirm('equipment',response)
        self.assertTrue(EquipmentType.objects.filter(pk=self.type.pk).exists());self.assertTrue(Equipment.objects.exists())
