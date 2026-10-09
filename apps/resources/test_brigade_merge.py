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
from apps.production.models import LaborPlan, LaborFact
from apps.rotation.models import RotationPlan, RotationRole, RotationPerson, RotationStatus
from .models import Brigade, BrigadeGroup, BrigadeMerge
from .brigade_merge import merge_brigades, brigade_aliases

JAN=date(2026,1,1)

class BrigadeMergeTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    create=fixtures.WorkspaceTests.create
    approve=fixtures.WorkspaceTests.approve

    def setUp(self):
        self.source=Brigade.objects.create(company=self.company,name=self.brigade.name,
            group=BrigadeGroup.objects.create(company=self.company,name='Other'))
        self.client.force_login(self.planner)

    def sheet(self):
        return {'name':self.obj.name,'warnings':[],'entries':[
            {'kind':'labor','name':self.brigade.name,'section':'Other','row':312,'unit':'чел.',
             'quantity':'3','month':'2026-01-01'}]}

    def test_catalog_and_file_duplicates_use_first_and_warn(self):
        sheet=self.sheet();sheet['entries'].append(dict(sheet['entries'][0],row=314,quantity='9'))
        _,rows=resolve_sheet(self.company,self.obj.project,sheet,self.obj)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['target_id'],self.brigade.pk)
        self.assertEqual(rows[0]['quantity'],'3')
        self.assertTrue(any('2 записей' in w for w in sheet['warnings']))
        self.assertTrue(any('314' in w and '312' in w for w in sheet['warnings']))
        sheet['entries'].append(dict(sheet['entries'][0],month='2026-02-01',row=316))
        _,rows=resolve_sheet(self.company,self.obj.project,sheet,self.obj)
        self.assertEqual(len(rows),2)

    def test_transfer_counts_hours_and_rotation_statuses(self):
        ws=self.create()
        for typ,count in [(self.brigade,2),(self.source,3)]:
            LaborPlan.objects.create(company=self.company,construction_object=self.obj,date=JAN,
                brigade=typ,planned_workers=count,planned_hours=count,hourly_rate=10)
            LaborFact.objects.create(company=self.company,construction_object=self.obj,date=JAN,
                brigade=typ,actual_workers=count,actual_hours=count,hourly_rate=10)
            ResourceMonthAllocation.objects.create(company=self.company,version=ws.baseline_version,
                kind='labor',month=JAN,brigade=typ,count=count,hours=count,rate=10)
        plan=RotationPlan.objects.create(company=self.company,source=ws.baseline_version,title='Rotation',
            start=JAN,end=date(2026,1,31),demand=[{'brigade_id':self.brigade.pk,'date':JAN.isoformat(),'count':2},
                                               {'brigade_id':self.source.pk,'date':JAN.isoformat(),'count':3}])
        target=RotationRole.objects.create(company=self.company,plan=plan,brigade=self.brigade,anchor=JAN)
        old=RotationRole.objects.create(company=self.company,plan=plan,brigade=self.source,anchor=JAN,on_days=30,off_days=60)
        person=RotationPerson.objects.create(company=self.company,position=old,name='Worker',anchor=JAN,on_days=20,off_days=40)
        status=RotationStatus.objects.create(company=self.company,person=person,day=JAN,status='OFF')
        merge_brigades(self.planner,self.brigade.pk,[self.source.pk])
        self.assertEqual(LaborPlan.objects.get().planned_workers,5)
        self.assertEqual(LaborFact.objects.get().actual_workers,5)
        self.assertEqual(LaborFact.objects.get().actual_hours,5)
        self.assertEqual(ws.baseline_version.resource_allocations.get().count,5)
        person.refresh_from_db();status.refresh_from_db();plan.refresh_from_db()
        self.assertEqual(person.position,target);self.assertEqual((person.on_days,person.off_days),(20,40))
        self.assertEqual(status.status,'OFF');self.assertEqual(plan.demand,[{'brigade_id':self.brigade.pk,'date':JAN.isoformat(),'count':5}])
        self.source.refresh_from_db();self.assertFalse(self.source.is_active)
        self.assertTrue(BrigadeMerge.objects.get().audit['records'])

    def test_rates_conflict_rolls_back(self):
        for typ,rate in [(self.brigade,10),(self.source,20)]:
            LaborFact.objects.create(company=self.company,construction_object=self.obj,date=JAN,
                brigade=typ,actual_workers=2,hourly_rate=rate)
        with self.assertRaisesMessage(ValidationError,'Разные ставки'):
            merge_brigades(self.planner,self.brigade.pk,[self.source.pk])
        self.assertEqual(LaborFact.objects.count(),2);self.assertFalse(BrigadeMerge.objects.exists())

    def test_frozen_plan_forecast_and_rotation_demand_keep_one_position(self):
        from apps.rotation.services import get_demand
        ws=self.create()
        for typ,count in [(self.brigade,2),(self.source,3)]:
            for month in [JAN,date(2026,2,1)]:
                ResourceMonthAllocation.objects.create(company=self.company,version=ws.baseline_version,
                    month=month,kind='labor',brigade=typ,count=count)
        self.approve(ws.baseline_version);ws.baseline_version.refresh_from_db()
        snapshot=deepcopy(ws.baseline_version.snapshot)
        merge_brigades(self.planner,self.brigade.pk,[self.source.pk])
        ws.baseline_version.refresh_from_db();self.assertEqual(ws.baseline_version.snapshot,snapshot)
        forecast=WorkspaceService.forecast(ws,self.planner,date(2026,2,1),'BASELINE')
        self.assertEqual(forecast.resource_allocations.get(kind='labor').count,5)
        self.assertEqual(forecast.resource_allocations.get(kind='labor').brigade_id,self.brigade.pk)
        GlobalPlanService.transition(forecast,self.planner,'submit')
        plan=RotationPlan.objects.create(company=self.company,source=ws.baseline_version,title='Demand',start=JAN,end=date(2026,1,31))
        demand=get_demand(plan)
        self.assertTrue(all(r['brigade_id']==self.brigade.pk and r['count']==5 for r in demand))

    def test_list_selection_confirmation_and_permissions(self):
        response=self.client.get(reverse('resources:brigade_list'))
        self.assertContains(response,'Объединить выбранные')
        self.assertContains(response,'2 записей')
        response=self.client.post(reverse('resources:brigade_merge'),{'merge_stage':'select',
            'selected':[self.brigade.pk,self.source.pk]})
        self.assertEqual(response.context['form'].initial['sources'],[self.source.pk])
        self.assertFalse(BrigadeMerge.objects.exists())
        response=self.client.post(reverse('resources:brigade_merge'),{'target':self.brigade.pk,'sources':[self.source.pk]})
        self.assertEqual(response.status_code,200);self.assertFalse(BrigadeMerge.objects.exists())
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('resources:brigade_merge')).status_code,403)
        with self.assertRaises(PermissionDenied):merge_brigades(self.manager,self.brigade.pk,[self.source.pk])

    def test_foreign_company_and_alias_chain(self):
        from apps.accounts.models import Company
        foreign=Brigade.objects.create(company=Company.objects.create(name='foreign'),name='Worker')
        with self.assertRaises(ValidationError):merge_brigades(self.planner,self.brigade.pk,[foreign.pk])
        merge_brigades(self.planner,self.brigade.pk,[self.source.pk])
        final=Brigade.objects.create(company=self.company,name='Unified')
        merge_brigades(self.planner,final.pk,[self.brigade.pk])
        self.assertEqual(brigade_aliases(self.company),{self.source.pk:final.pk,self.brigade.pk:final.pk})

    def test_members_transfer_without_duplicate_employee(self):
        from .models import Employee,BrigadeMember
        employee=Employee.objects.create(company=self.company,first_name='Ivan',last_name='Ivanov')
        for typ,role in [(self.brigade,'Водитель'),(self.source,'Машинист')]:
            BrigadeMember.objects.create(company=self.company,brigade=typ,employee=employee,role_in_brigade=role)
        merge_brigades(self.planner,self.brigade.pk,[self.source.pk])
        member=BrigadeMember.objects.get()
        self.assertEqual(member.brigade_id,self.brigade.pk)
        self.assertIn('Водитель',member.role_in_brigade);self.assertIn('Машинист',member.role_in_brigade)
