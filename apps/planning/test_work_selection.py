from datetime import date
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.works.models import ComplexObject,ProjectWork
from . import test_workspace as fixtures
from . import test_daily_schedule as daily_fixtures
from .models import WorkMonthAllocation
from .workspace_services import WorkspaceService,build_workspace_snapshot
from .work_selection import resolve,extend_allocation
from .daily_schedule_table import add_entry,save_table

class BranchSelectionTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    def setUp(self):
        self.facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='УЗА 26')
        self.composite.complex_object=self.facility;self.composite.save()
        self.sibling=ProjectWork.objects.create(company=self.company,section=self.composite.section,kind='SIMPLE',name='Соседний комплекс',unit='шт')
        self.sibling.complex_object_id=None;self.sibling.save()
        self.workspace=WorkspaceService.create(self.planner,self.obj,'Ветки',fixtures.JAN,date(2026,3,31))
        self.version=self.workspace.baseline_version
        self.client.force_login(self.planner)
        self.url=reverse('planning:workspace_add_branch',args=[self.version.pk])

    def add(self,selection,month='2026-01-01'):
        return self.client.post(self.url,{'selection':selection,'months':[month]})

    def test_picker_options_include_structured_parent_ids(self):
        from .work_selection_views import BranchForm
        options={row['value']:row for row in BranchForm(version=self.version).catalogue_options}
        self.assertEqual(options[f'object:{self.facility.pk}']['object_id'],str(self.facility.pk))
        self.assertEqual(options[f'package:{self.composite.pk}']['object_id'],str(self.facility.pk))
        item=options[f'item:{self.a.pk}']
        self.assertEqual((item['object_id'],item['package_id']),(str(self.facility.pk),str(self.composite.pk)))
        self.assertEqual(options[f'package:{self.sibling.pk}']['object_id'],'')
        self.assertEqual(options[f'package:{self.sibling.pk}']['package_id'],'')

    def test_single_item_adds_only_its_parents_and_survives_snapshot(self):
        self.assertEqual(self.add(f'item:{self.a.pk}').status_code,302)
        row=self.version.work_allocations.get()
        self.assertEqual(row.work_id,self.composite.pk)
        self.assertEqual(row.selected_item_ids,[self.a.pk])
        row.quantity=10;row.save()
        spec=build_workspace_snapshot(self.version)['works'][0]
        self.assertEqual([i['id'] for i in spec['items']],[self.a.pk])
        self.assertEqual({r['item_id'] for r in spec['plans']},{self.a.pk})
        self.assertEqual(spec['complex_object_id'],self.facility.pk)
        # A partial branch must not claim completion/payment of the full package.
        self.assertEqual(sum(Decimal(r['quantity']) for r in spec['daily']),0)
        self.assertEqual(sum(Decimal(r['quantity']) for r in spec['plans']),20)

    def test_package_adds_all_items_without_neighboring_packages(self):
        self.add(f'package:{self.composite.pk}')
        self.assertEqual(list(self.version.work_allocations.values_list('work_id',flat=True)),[self.composite.pk])
        self.assertEqual(self.version.work_allocations.get().selected_item_ids,[])

    def test_object_adds_all_its_packages_but_not_external_work(self):
        second=ProjectWork.objects.create(company=self.company,section=self.composite.section,kind='COMPOSITE',complex_object=self.facility,name='Смежный комплекс',unit='шт')
        self.add(f'object:{self.facility.pk}')
        self.assertEqual(set(self.version.work_allocations.values_list('work_id',flat=True)),{self.composite.pk,second.pk})
        self.assertFalse(self.version.work_allocations.filter(work=self.sibling).exists())

    def test_repeated_addition_and_expansion_preserve_existing_child_volume(self):
        self.add(f'item:{self.a.pk}')
        row=self.version.work_allocations.get();row.quantity=10;row.save()
        self.add(f'item:{self.a.pk}')
        row.refresh_from_db();self.assertEqual(row.quantity,10)
        self.add(f'item:{self.b.pk}')
        row.refresh_from_db();self.assertEqual(set(row.selected_item_ids),{self.a.pk,self.b.pk})
        self.assertEqual(Decimal(row.item_quantities[str(self.a.pk)]),20)
        self.assertEqual(Decimal(row.item_quantities[str(self.b.pk)]),0)
        self.assertEqual(self.version.work_allocations.count(),1)

    def test_partial_scope_survives_form_save_and_excel_roundtrip(self):
        from .workspace_forms import WorkAllocationForm
        from .workspace_excel import make_workbook,import_workbook
        from io import BytesIO
        self.add(f'item:{self.a.pk}')
        row=self.version.work_allocations.get();row.quantity=10;row.save()
        form=WorkAllocationForm({'work':self.composite.pk,'month':'2026-01-01','quantity':'10','load_profile':''},instance=row,user=self.planner,version=self.version,month=fixtures.JAN)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        row.refresh_from_db();self.assertEqual(row.selected_item_ids,[self.a.pk])
        from django.core.files.uploadedfile import SimpleUploadedFile
        out=SimpleUploadedFile('plan.xlsx',make_workbook(self.version,[fixtures.JAN]))
        import_workbook(self.planner,self.version,out,[fixtures.JAN])
        row.refresh_from_db();self.assertEqual(row.selected_item_ids,[self.a.pk])
        self.assertEqual(Decimal(row.item_quantities[str(self.a.pk)]),20)

    def test_editor_changes_package_composition_and_keeps_child_volumes(self):
        from .workspace_forms import WorkAllocationForm
        self.add(f'package:{self.composite.pk}')
        row=self.version.work_allocations.get()
        WorkMonthAllocation.objects.filter(pk=row.pk).update(daily_override=[{'date':'2026-01-05','quantity':'1'}])
        row.refresh_from_db()
        payload={'work':self.composite.pk,'month':'2026-01-01','quantity':'10','load_profile':'',
                 'selected_item_ids':f'[{self.a.pk}]','edited_item_quantities':f'{{"{self.a.pk}":"20"}}'}
        form=WorkAllocationForm(payload,instance=row,user=self.planner,version=self.version,month=fixtures.JAN)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        row.refresh_from_db()
        self.assertEqual(row.selected_item_ids,[self.a.pk])
        self.assertEqual(row.item_quantities,{str(self.a.pk):'20'})
        self.assertIsNone(row.daily_override)
        payload.update(selected_item_ids='[]',edited_item_quantities=f'{{"{self.a.pk}":"20","{self.b.pk}":"30"}}')
        form=WorkAllocationForm(payload,instance=row,user=self.planner,version=self.version,month=fixtures.JAN)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        row.refresh_from_db();self.assertEqual(row.selected_item_ids,[])
        self.assertEqual(row.item_quantities[str(self.a.pk)],'20')

    def test_branch_expansion_retains_daily_override(self):
        self.add(f'item:{self.a.pk}')
        row=self.version.work_allocations.get()
        daily=[{'date':'2026-01-05','item_id':self.a.pk,'quantity':'17','value':'0','origin':'PLAN'}]
        row.daily_override=daily;row.save()
        self.add(f'item:{self.b.pk}')
        row.refresh_from_db()
        self.assertEqual(row.daily_override,daily)
        self.assertEqual(Decimal(row.item_quantities[str(self.a.pk)]),17)

    def test_partial_child_volume_does_not_complete_package_until_all_norms_are_met(self):
        from .workspace_forms import WorkAllocationForm
        self.a.quantity_per_unit=12
        self.a.save()
        self.add(f'item:{self.a.pk}')
        row=self.version.work_allocations.get()
        payload={'work':self.composite.pk,'month':'2026-01-01','quantity':'17.916','load_profile':'',
                 'selected_item_ids':f'[{self.a.pk}]','edited_item_quantities':f'{{"{self.a.pk}":"215"}}'}
        form=WorkAllocationForm(payload,instance=row,user=self.planner,version=self.version,month=fixtures.JAN)
        self.assertTrue(form.is_valid(),form.errors)
        form.save()
        row.refresh_from_db()
        self.assertEqual(row.quantity,0)
        self.assertEqual(Decimal(row.item_quantities[str(self.a.pk)]),215)
        spec=build_workspace_snapshot(self.version)['works'][0]
        self.assertEqual(sum(Decimal(r['quantity']) for r in spec['daily']),0)
        self.assertEqual(sum(Decimal(r['quantity']) for r in spec['plans']),215)

        # Merely adding the missing work does not complete any packages.
        extend_allocation(row,[self.b.pk])
        row.refresh_from_db()
        self.assertEqual(row.quantity,0)
        self.assertEqual(Decimal(row.item_quantities[str(self.a.pk)]),215)
        self.assertEqual(Decimal(row.item_quantities[str(self.b.pk)]),0)
        row.item_quantities[str(self.b.pk)]='60'
        row.save()
        row.refresh_from_db()
        self.assertEqual(row.quantity,Decimal('17.916'))
        self.assertEqual(sum(Decimal(r['quantity']) for r in build_workspace_snapshot(self.version)['works'][0]['daily']),Decimal('17.916'))

    def test_partial_remainder_is_distributed_by_child_volume(self):
        from .work_selection import partial_remainder_rows
        from .workspace_services import specification
        self.version.planning_month=fixtures.FEB
        spec=specification(self.composite,[self.a.pk])
        spec['plans']=[{'date':'2026-03-05','item_id':self.a.pk,'quantity':'20','value':'0'}]
        rows,context=partial_remainder_rows(self.version,spec,[],[],[fixtures.MAR])
        self.assertEqual(sum(Decimal(row['quantity']) for row in rows),20)
        self.assertEqual({row['item_id'] for row in rows},{self.a.pk})

    def test_remainder_cannot_silently_disappear_outside_future_selection(self):
        from .work_selection import partial_remainder_rows
        from .workspace_services import specification
        self.version.planning_month=fixtures.FEB
        spec=specification(self.composite,[self.a.pk,self.b.pk])
        spec['plans']=[{'date':'2026-03-05','item_id':self.a.pk,'quantity':'20','value':'0'}]
        future=WorkMonthAllocation(company=self.company,version=self.version,work=self.composite,month=fixtures.MAR,selected_item_ids=[self.b.pk])
        with self.assertRaises(ValidationError):partial_remainder_rows(self.version,spec,[],[future],[fixtures.MAR])

    def test_model_rejects_foreign_selection_and_outside_child_volume(self):
        row=WorkMonthAllocation(company=self.company,version=self.version,work=self.composite,month=fixtures.JAN,selected_item_ids=[999999])
        with self.assertRaises(ValidationError):row.full_clean()
        row.selected_item_ids=[self.a.pk];row.item_quantities={str(self.b.pk):'10'}
        with self.assertRaises(ValidationError):row.full_clean()

    def test_selection_visible_on_editor_and_scope_and_status_enforced(self):
        response=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        for label in ['Комплексный объект','Комплексная работа','Работа']:
            self.assertContains(response,label)
        self.assertNotContains(response,'Добавить из справочника: объект → комплекс → работа')
        from .work_selection_views import BranchForm
        options={row['value']:row['level'] for row in BranchForm(version=self.version).catalogue_options}
        self.assertEqual(options[f'object:{self.facility.pk}'],'object')
        self.assertEqual(options[f'package:{self.composite.pk}'],'package')
        self.assertEqual(options[f'item:{self.a.pk}'],'work')
        self.assertEqual(options[f'package:{self.sibling.pk}'],'work')
        self.planner.assigned_objects.clear()
        self.assertEqual(self.add(f'item:{self.a.pk}').status_code,404)

class DailyBranchSelectionTests(TestCase):
    setUpTestData=classmethod(daily_fixtures.DailyScheduleTests.setUpTestData.__func__)
    prepare=daily_fixtures.DailyScheduleTests.prepare
    def test_smg_form_adds_a_selected_work(self):
        self.prepare()
        url=reverse('planning:daily_schedule_detail',args=[self.schedule.pk])
        response=self.client.post(url,{'kind':'work','selection':f'item:{self.a.pk}'})
        self.assertEqual(response.status_code,302)
        entry=self.schedule.entries.get(key='work:'+str(self.composite.pk))
        self.assertEqual([item['id'] for item in entry.metadata['items']],[self.a.pk])

    def test_creation_and_next_revision_retain_monthly_branch(self):
        from .workspace_services import specification
        from .models import GlobalPlanVersion,DailySchedule
        from .daily_schedule import create
        from copy import deepcopy
        workspace=WorkspaceService.create(self.planner,self.obj,'Частичный ГПР',fixtures.JAN,date(2026,3,31))
        source=workspace.baseline_version
        spec=specification(self.composite,[self.a.pk,self.b.pk])
        spec.update(plans=[{'date':'2026-02-01','item_id':self.a.pk,'quantity':'20','value':'40','origin':'PLAN'},{'date':'2026-03-01','item_id':self.b.pk,'quantity':'30','value':'60','origin':'PLAN'}],daily=[],versions=[])
        GlobalPlanVersion.objects.filter(pk=source.pk).update(status='APPROVED',snapshot={'works':[spec],'resources':{'labor':[],'equipment':[],'fuel':[]}})
        source.refresh_from_db()
        schedule=create(self.planner,source,fixtures.FEB)
        entry=schedule.entries.get(kind='work')
        self.assertEqual([item['id'] for item in entry.metadata['items']],[self.a.pk])
        add_entry(self.planner,schedule,{'kind':'work','selection':f'item:{self.b.pk}'})
        # Seed an applied fixture; this test concerns copying a revision, not approvals.
        DailySchedule.objects.filter(pk=schedule.pk).update(status='APPLIED')
        revision=create(self.planner,source,fixtures.FEB)
        copied=revision.entries.get(kind='work')
        self.assertEqual({item['id'] for item in copied.metadata['items']},{self.a.pk,self.b.pk})
        self.assertEqual(sum(Decimal(row['quantity']) for row in copied.rows if row['item_id']==self.a.pk),20)

    def test_partial_smg_addition_and_expansion_preserve_day_values(self):
        self.prepare()
        result=add_entry(self.planner,self.schedule,{'kind':'work','selection':f'item:{self.a.pk}'})
        entry=result[0]
        self.assertEqual([i['id'] for i in entry.metadata['items']],[self.a.pk])
        self.assertEqual({r['item_id'] for r in entry.rows},{self.a.pk})
        save_table(self.planner,self.schedule,[{'id':entry.pk,'revision':entry.revision,'changes':[{'date':'2026-02-02','item_id':self.a.pk,'field':'quantity','value':'7'}]}])
        add_entry(self.planner,self.schedule,{'kind':'work','selection':f'item:{self.b.pk}'})
        entry.refresh_from_db()
        self.assertEqual(set(i['id'] for i in entry.metadata['items']),{self.a.pk,self.b.pk})
        self.assertEqual(sum(Decimal(r['quantity']) for r in entry.rows if r['item_id']==self.a.pk),7)
        self.assertEqual(sum(Decimal(r['quantity']) for r in entry.rows if r['item_id']==self.b.pk),0)
        self.assertEqual(self.schedule.entries.filter(key=entry.key).count(),1)
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)
