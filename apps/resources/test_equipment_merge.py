from copy import deepcopy
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase
from django.urls import reverse
from apps.planning import test_workspace as fixtures
from apps.planning.meeting_import import resolve_sheet
from apps.planning.models import ResourceMonthAllocation
from apps.planning.workspace_services import WorkspaceService
from apps.planning.global_services import GlobalPlanService
from apps.planning.report_services import Source, build_matrix
from apps.production.models import EquipmentPlan, EquipmentFact
from .models import EquipmentType, EquipmentCategory, Equipment, EquipmentTypeMerge
from .equipment_merge import merge_equipment, normalize_equipment_snapshot, equipment_aliases


class EquipmentMergeTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    create = fixtures.WorkspaceTests.create
    approve = fixtures.WorkspaceTests.approve

    def setUp(self):
        self.source = EquipmentType.objects.create(company=self.company, name=self.equipment.name,
            category=EquipmentCategory.objects.create(company=self.company, name='Other category'))
        self.client.force_login(self.planner)

    def sheet(self):
        return {'name': self.obj.name, 'warnings': [], 'entries': [
            {'kind':'equipment', 'name':self.equipment.name, 'section':self.source.category.name,
             'row':33, 'unit':'ед.', 'quantity':'2', 'month':'2026-01-01', 'equipment_number':''}]}

    def test_import_uses_first_id_across_categories_and_warns(self):
        sheet = self.sheet()
        _, rows = resolve_sheet(self.company, self.obj.project, sheet, self.obj)
        self.assertEqual(rows[0]['target_id'], self.equipment.pk)
        self.assertIn('2 записей', sheet['warnings'][0])
        self.assertIn(f'ID {self.equipment.pk}', sheet['warnings'][0])
        resolve_sheet(self.company, self.obj.project, sheet, self.obj)
        self.assertEqual(len(sheet['warnings']), 1)

    def test_transfer_merges_colliding_records_and_keeps_machine_numbers(self):
        machine = Equipment.objects.create(company=self.company, type=self.source, name='PRM', plate_number='01')
        for typ, count in [(self.equipment,2),(self.source,3)]:
            EquipmentPlan.objects.create(company=self.company, construction_object=self.obj, equipment_type=typ,
                date=date(2026,1,1), planned_count=count, planned_machine_hours=Decimal(count), hourly_rate=10)
            EquipmentFact.objects.create(company=self.company, construction_object=self.obj, equipment_type=typ,
                date=date(2026,1,1), actual_count=count, machine_hours=Decimal(count), hourly_rate=10)
        EquipmentFact.objects.create(company=self.company, construction_object=self.obj, equipment_type=self.source,
            date=date(2026,1,1), equipment_number='02', actual_count=1)
        workspace = self.create()
        for typ, count in [(self.equipment,2),(self.source,3)]:
            ResourceMonthAllocation.objects.create(company=self.company, version=workspace.baseline_version,
                month=date(2026,1,1), kind='equipment', equipment_type=typ, count=count, hours=count, rate=10)
        merge_equipment(self.planner, self.equipment.pk, [self.source.pk])
        self.assertEqual(EquipmentPlan.objects.get().planned_count,5)
        self.assertEqual(EquipmentFact.objects.get(equipment_number='').actual_count,5)
        self.assertEqual(EquipmentFact.objects.get(equipment_number='02').equipment_type,self.equipment)
        self.assertEqual(workspace.baseline_version.resource_allocations.get().count,5)
        machine.refresh_from_db();self.assertEqual(machine.type,self.equipment)
        self.source.refresh_from_db();self.assertFalse(self.source.is_active)
        link=EquipmentTypeMerge.objects.get();self.assertEqual(link.created_by,self.planner)
        self.assertTrue(link.audit['records'])
        self.assertNotIn(self.source.pk, [r.pk for r in self.client.get(reverse('resources:equipment_type_list')).context['equipment_types']])

    def test_different_rates_roll_back_everything(self):
        for typ, rate in [(self.equipment,10),(self.source,20)]:
            EquipmentFact.objects.create(company=self.company, construction_object=self.obj, equipment_type=typ,
                date=date(2026,1,1), actual_count=2, hourly_rate=rate)
        with self.assertRaisesMessage(ValidationError,'Разные ставки'):
            merge_equipment(self.planner,self.equipment.pk,[self.source.pk])
        self.assertEqual(EquipmentFact.objects.count(),2)
        self.assertFalse(EquipmentTypeMerge.objects.exists())
        self.source.refresh_from_db();self.assertTrue(self.source.is_active)

    def test_frozen_snapshot_is_preserved_and_forecast_uses_one_type(self):
        workspace=self.create()
        for typ, count in [(self.equipment,2),(self.source,3)]:
            for month in [date(2026,1,1),date(2026,2,1)]:
                ResourceMonthAllocation.objects.create(company=self.company,version=workspace.baseline_version,
                    month=month,kind='equipment',equipment_type=typ,count=count,
                    hours=1 if typ == self.equipment else 2, rate=10 if typ == self.equipment else 11)
        self.approve(workspace.baseline_version)
        workspace.baseline_version.refresh_from_db()
        before=deepcopy(workspace.baseline_version.snapshot)
        merge_equipment(self.planner,self.equipment.pk,[self.source.pk])
        workspace.baseline_version.refresh_from_db();self.assertEqual(workspace.baseline_version.snapshot,before)
        self.assertEqual(workspace.baseline_version.resource_allocations.filter(equipment_type=self.source).count(),2)
        normalized=normalize_equipment_snapshot(before,equipment_aliases(self.company))
        self.assertTrue(all(r['equipment_type_id']==self.equipment.pk for r in normalized['resources']['equipment']))
        forecast=WorkspaceService.forecast(workspace,self.planner,date(2026,2,1),'BASELINE')
        self.assertEqual(forecast.resource_allocations.get(kind='equipment').count,5)
        self.assertEqual(forecast.resource_allocations.get(kind='equipment').rate,Decimal('10.67'))
        self.assertEqual(forecast.resource_allocations.get(kind='equipment').equipment_type_id,self.equipment.pk)
        GlobalPlanService.transition(forecast,self.planner,'submit')

    def test_authorization_company_and_confirmation(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('resources:equipment_type_merge')).status_code,403)
        with self.assertRaises(PermissionDenied): merge_equipment(self.manager,self.equipment.pk,[self.source.pk])
        self.client.force_login(self.planner)
        response=self.client.post(reverse('resources:equipment_type_merge'),{'target':self.equipment.pk,'sources':[self.source.pk]})
        self.assertEqual(response.status_code,200);self.assertFalse(EquipmentTypeMerge.objects.exists())
        with self.assertRaises(ValidationError): merge_equipment(self.planner,self.equipment.pk,[999999])

    def test_foreign_company_unit_mismatch_and_alias_chains(self):
        from apps.accounts.models import Company
        foreign=EquipmentType.objects.create(company=Company.objects.create(name='foreign'),name='ПРМ')
        with self.assertRaises(ValidationError): merge_equipment(self.planner,self.equipment.pk,[foreign.pk])
        self.source.unit='ч';self.source.save()
        with self.assertRaises(ValidationError): merge_equipment(self.planner,self.equipment.pk,[self.source.pk])
        self.assertFalse(EquipmentTypeMerge.objects.exists())
        self.source.unit=self.equipment.unit;self.source.save()
        merge_equipment(self.planner,self.equipment.pk,[self.source.pk])
        final=EquipmentType.objects.create(company=self.company,name='Unified',unit=self.equipment.unit)
        merge_equipment(self.planner,final.pk,[self.equipment.pk])
        self.assertEqual(equipment_aliases(self.company),{self.source.pk:final.pk,self.equipment.pk:final.pk})
        sheet=self.sheet()
        sheet['entries'][0]['name']=final.name
        _,rows=resolve_sheet(self.company,self.obj.project,sheet,self.obj)
        self.assertEqual(rows[0]['target_id'],final.pk)

    def test_frozen_plan_fact_report_keeps_comparison_after_merge(self):
        from apps.planning.test_report_matrix import rows_in
        workspace=self.create()
        for typ in [self.equipment,self.source]:
            ResourceMonthAllocation.objects.create(company=self.company,version=workspace.baseline_version,
                month=date(2026,1,1),kind='equipment',equipment_type=typ,count=2)
            EquipmentFact.objects.create(company=self.company,construction_object=self.obj,date=date(2026,1,1),
                equipment_type=typ,actual_count=3)
        self.approve(workspace.baseline_version)
        workspace.baseline_version.refresh_from_db()
        merge_equipment(self.planner,self.equipment.pk,[self.source.pk])
        version=workspace.baseline_version
        report=build_matrix(self.planner,{'start':date(2026,1,1),'end':date(2026,1,31),'sections':['equipment']},
            source_overrides={self.obj.pk:[Source(version,version.snapshot,version.start_date,version.end_date)]})
        row=rows_in(report['objects'][0]['sections'][0]['groups'])[0]
        self.assertEqual(row['cells'][0]['plan'],4)
        self.assertEqual(row['cells'][0]['fact'],6)
