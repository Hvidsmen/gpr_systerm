from copy import deepcopy
from datetime import date
from decimal import Decimal
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError,PermissionDenied
from apps.projects.models import ConstructionObject,Section
from apps.works.models import ProjectWork
from apps.production.models import DailyFact,LaborFact
from . import test_workspace as fixtures
from .models import WorkMonthAllocation,ResourceMonthAllocation
from .workspace_services import WorkspaceService,build_workspace_snapshot
from .macro_history import create_macro
from .hierarchy_services import create_smg,month_specs
from .report_services import build_matrix
from .test_report_matrix import rows_in

MAR=fixtures.MAR


class MacroHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures.WorkspaceTests.setUpTestData.__func__(cls)
    approve=fixtures.WorkspaceTests.approve

    def prepare(self):
        self.children=[];self.sources=[];self.child_works=[]
        for n in [1,2]:
            child=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name=f'КТП-{n}')
            section=Section.objects.create(company=self.company,construction_object=child,name='History')
            work=ProjectWork.objects.create(company=self.company,section=section,name=f'Work {n}',unit='m',unit_price=10)
            self.planner.assigned_objects.add(child)
            w=WorkspaceService.create(self.planner,child,f'Old {n}',fixtures.JAN,date(2026,3,31))
            for month,quantity in [(fixtures.JAN,100*n),(fixtures.FEB,100*n),(MAR,0)]:WorkMonthAllocation.objects.create(company=self.company,version=w.baseline_version,work=work,month=month,quantity=quantity)
            ResourceMonthAllocation.objects.create(company=self.company,version=w.baseline_version,month=fixtures.JAN,kind='labor',brigade=self.brigade,count=n,hours=8,rate=10)
            version=self.approve(w.baseline_version)
            self.children.append(child);self.sources.append(version);self.child_works.append(work)
        self.original=[deepcopy(v.snapshot) for v in self.sources]
        self.workspace=create_macro(self.planner,self.obj,self.sources,'КТП общий',fixtures.JAN,MAR,date(2026,3,31))
        self.version=self.workspace.baseline_version

    def filters(self,**extra):return {'start':fixtures.JAN,'end':date(2026,3,31),'mode':'latest','sections':['works','labor'],**extra}

    def test_frozen_history_and_empty_new_month_and_ui(self):
        self.prepare()
        snap=build_workspace_snapshot(self.version)
        self.assertFalse(self.version.work_allocations.exists())
        self.assertEqual(len(snap['macro_history_sources']),2)
        self.assertEqual(sum(Decimal(r['quantity']) for s in snap['works'] for r in s['daily']),600)
        self.assertTrue(all(r['date']<'2026-03-01' for s in snap['works'] for r in s['plans']))
        for source,original,child in zip(self.sources,self.original,self.children):
            source.refresh_from_db();self.assertEqual(source.snapshot,original)
            child.refresh_from_db();self.assertEqual(child.parent_id,self.obj.pk)
        self.client.force_login(self.planner)
        self.assertContains(self.client.get(reverse('planning:macro_history_create')),'Объединить ГПР')
        self.assertContains(self.client.get(reverse('planning:macro_history_detail',args=[self.workspace.pk])),'История подключена')
        editor=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertEqual(editor.context['selected_month'],MAR)
        self.assertContains(self.client.get(reverse('planning:workspace_gpr_import',args=[self.version.pk])+'?scope=month&month=2026-03-01'),'ГПР')

    def test_report_ownership_and_historical_actuals_count_once(self):
        self.prepare()
        for n,work,child in zip([1,2],self.child_works,self.children):
            DailyFact.objects.create(company=self.company,project_work=work,date=fixtures.JAN,actual_quantity=10*n)
            LaborFact.objects.create(company=self.company,construction_object=child,brigade=self.brigade,date=fixtures.JAN,actual_workers=n)
        self.version=self.approve(self.version)
        report=build_matrix(self.planner,self.filters())
        self.assertEqual([o['object'].pk for o in report['objects']],[self.obj.pk])
        work_rows=[r for s in report['objects'][0]['sections'] if s['kind']=='works' for r in rows_in(s['groups'])]
        self.assertEqual(sum(r['total']['plan'] for r in work_rows),600)
        self.assertEqual(sum(r['total']['fact'] or 0 for r in work_rows),30)
        single=build_matrix(self.planner,self.filters(objects=ConstructionObject.objects.filter(pk=self.children[0].pk)))
        self.assertEqual([o['object'].pk for o in single['objects']],[self.children[0].pk])
        macro_only=build_matrix(self.planner,self.filters(objects=ConstructionObject.objects.filter(pk=self.obj.pk)))
        work_rows=[r for s in macro_only['objects'][0]['sections'] if s['kind']=='works' for r in rows_in(s['groups'])]
        self.assertEqual(sum(r['total']['fact'] or 0 for r in work_rows),30)

    def test_new_macro_plan_manual_smg_distribution(self):
        self.prepare()
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=MAR,quantity=50)
        self.version=self.approve(self.version)
        specs=month_specs(self.version,MAR)
        self.assertEqual([s['id'] for s in specs],[self.simple.pk])
        first=create_smg(self.planner,self.version,self.children[0],MAR,{self.simple.pk:30})
        second=create_smg(self.planner,self.version,self.children[1],MAR,{self.simple.pk:20})
        self.assertEqual(first.parent_gpr_id,self.version.pk)
        self.assertEqual(second.baseline_version.work_allocations.get().quantity,20)
        self.client.force_login(self.planner)
        self.assertContains(self.client.get(reverse('planning:macro_history_detail',args=[self.workspace.pk])),'ГПР → СМГ')

    def test_wrong_sources_and_roles_are_rejected(self):
        self.prepare()
        with self.assertRaises(ValidationError):create_macro(self.planner,self.obj,[self.sources[0],self.sources[0]],'Duplicate',fixtures.JAN,MAR,date(2026,3,31))
        with self.assertRaises(PermissionDenied):create_macro(self.manager,self.obj,self.sources,'No',fixtures.JAN,MAR,date(2026,3,31))
        with self.assertRaises(ValidationError):create_macro(self.planner,self.obj,self.sources,'Bad date',fixtures.JAN,date(2026,3,2),date(2026,3,31))

    def test_excel_upload_into_new_macro_month_and_history_readonly(self):
        from .gpr_excel import parse_gpr
        from .gpr_excel_views import apply_gpr
        from .test_gpr_excel import classic
        from .meeting_import import validate_target_period
        self.prepare()
        payload=parse_gpr(classic(),'macro.xlsx')
        # The existing upload confirmation maps selected Excel columns to target months.
        keep=payload['months'][0]['column']
        payload['entries']=[r for r in payload['entries'] if r['column']==keep]
        payload['months']=[r for r in payload['months'] if r['column']==keep]
        apply_gpr(self.planner,self.obj,payload,{f'month_{keep}':MAR},str(uuid4()),target_version=self.version)
        self.assertTrue(self.version.work_allocations.filter(month=MAR).exists())
        self.assertFalse(self.version.work_allocations.filter(month__lt=MAR).exists())
        snapshot=build_workspace_snapshot(self.version)
        self.assertEqual(len(snapshot['macro_history_sources']),2)
        self.assertTrue(any(r['date']>='2026-03-01' for s in snapshot['works'] if not s.get('history_source_object_id') for r in s['plans']))
        with self.assertRaises(ValidationError):validate_target_period(self.version,fixtures.JAN,date(2026,1,31))

    def test_create_wizard_and_sources_are_not_hidden_before_approval(self):
        self.prepare()
        report=build_matrix(self.planner,self.filters())
        self.assertTrue({c.pk for c in self.children}<={o['object'].pk for o in report['objects']})
        self.client.force_login(self.planner)
        response=self.client.post(reverse('planning:macro_history_create'),{'macro':self.obj.pk,'sources':[s.pk for s in self.sources],'name':'From wizard','start':'2026-01-01','new_start':'2026-03-01','end':'2026-03-31'})
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(response.url),'История подключена')

    def test_partial_period_and_resource_actuals_count_once(self):
        from apps.production.models import EquipmentFact
        self.prepare()
        for n,child in zip([1,2],self.children):
            LaborFact.objects.create(company=self.company,construction_object=child,brigade=self.brigade,date=fixtures.JAN,actual_workers=n)
            EquipmentFact.objects.create(company=self.company,construction_object=child,equipment_type=self.equipment,date=fixtures.JAN,actual_count=n)
        DailyFact.objects.create(company=self.company,project_work=self.child_works[0],date=date(2026,4,1),actual_quantity=8)
        self.version=self.approve(self.version)
        report=build_matrix(self.planner,self.filters(end=date(2026,4,1),sections=['works','labor','equipment']))
        macro=next(o for o in report['objects'] if o['object'].pk==self.obj.pk)
        for kind in ['labor','equipment']:
            rows=[r for s in macro['sections'] if s['kind']==kind for r in rows_in(s['groups'])]
            self.assertTrue(any(r['total']['fact']==3 for r in rows),rows)
        child=next(o for o in report['objects'] if o['object'].pk==self.children[0].pk)
        rows=[r for s in child['sections'] if s['kind']=='works' for r in rows_in(s['groups'])]
        self.assertEqual(sum(r['total']['fact'] or 0 for r in rows),8)
