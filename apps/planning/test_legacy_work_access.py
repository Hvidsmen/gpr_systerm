from datetime import date
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.accounts.models import User,Role
from apps.works.models import ProjectWork,ProjectWorkItem,ComplexObject
from . import test_workspace as fixtures
from .workspace_services import WorkspaceService
from .work_selection import choices,resolve
from .models import DailySchedule,WorkMonthAllocation
from .daily_schedule_table import add_entry,add_work_branch
from .work_selection_views import BranchForm,ScheduleBranchForm
from .daily_schedule_table_forms import AddEntryForm
from .workspace_forms import AddWorkForm,WorkAllocationForm
from .bulk_add import BulkAddForm

JAN=fixtures.JAN


class LegacyPlanningAccessTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        self.admin=User.objects.create_user(username='legacy-plan-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.workspace=WorkspaceService.create(self.planner,self.obj,'Legacy choices',JAN,date(2026,3,31))
        self.version=self.workspace.baseline_version
        self.schedule=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=JAN,revision=1,created_by=self.planner)
        self.facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,name='Facility')
        ProjectWork.objects.filter(pk__in=[self.simple.pk,self.composite.pk]).update(complex_object=self.facility)
        ProjectWork.objects.filter(pk=self.simple.pk).update(is_legacy_structure=True)
        ProjectWorkItem.objects.filter(pk=self.a.pk).update(is_legacy_structure=True)
        self.simple.refresh_from_db();self.a.refresh_from_db()
        self.works=ProjectWork.objects.filter(company=self.company)

    def tokens(self,options):
        return {token for _,group in options if isinstance(group,list) for token,_ in group}

    def test_gpr_smg_and_fallback_pickers_apply_roles(self):
        for user,allowed in [(self.planner,False),(self.admin,True)]:
            for form in [BranchForm(version=self.version,user=user),ScheduleBranchForm(schedule=self.schedule,user=user),AddEntryForm(schedule=self.schedule,user=user)]:
                tokens=self.tokens(form.fields['selection'].choices)
                self.assertEqual(f'package:{self.simple.pk}' in tokens,allowed)
                self.assertEqual(f'item:{self.a.pk}' in tokens,allowed)
                self.assertIn(f'item:{self.b.pk}',tokens)
            for form in [AddWorkForm(workspace=self.workspace,user=user),BulkAddForm(version=self.version,kind='works',user=user)]:
                field=form.fields['work' if 'work' in form.fields else 'items']
                self.assertEqual(field.queryset.filter(pk=self.simple.pk).exists(),allowed)
                self.assertEqual(field.queryset.filter(pk=self.composite.pk).exists(),allowed)

    def test_resolver_blocks_forged_tokens_and_filters_entire_object(self):
        for token in [f'package:{self.simple.pk}',f'item:{self.a.pk}']:
            with self.assertRaises(ValidationError):resolve(self.works,token,user=self.planner)
            self.assertTrue(resolve(self.works,token,user=self.admin))
        rows=resolve(self.works,f'object:{self.facility.pk}',user=self.planner)
        self.assertEqual([(work.pk,ids) for work,ids in rows],[(self.composite.pk,[self.b.pk])])
        self.assertEqual(resolve(self.works,f'package:{self.composite.pk}',user=self.admin)[0][1],[])

    def test_gpr_endpoint_rejects_forged_selection_and_keeps_normal_components(self):
        self.client.force_login(self.planner)
        url=reverse('planning:workspace_add_branch',args=[self.version.pk])
        self.client.post(url,{'selection':f'package:{self.simple.pk}','months':[JAN.isoformat()]})
        self.assertFalse(self.version.work_allocations.exists())
        self.client.post(url,{'selection':f'object:{self.facility.pk}','months':[JAN.isoformat()]})
        row=self.version.work_allocations.get();self.assertEqual(row.selected_item_ids,[self.b.pk])
        self.client.force_login(self.admin)
        self.client.post(url,{'selection':f'package:{self.simple.pk}','months':[JAN.isoformat()]})
        self.assertTrue(self.version.work_allocations.filter(work=self.simple).exists())

    def test_smg_service_enforces_roles_for_branches_hidden_ids_and_item_override(self):
        for payload in [{'kind':'work','work':self.simple},{'kind':'work','selection':f'package:{self.simple.pk}'}]:
            with self.assertRaises(ValidationError):add_entry(self.planner,self.schedule,payload)
        with self.assertRaises(ValidationError):add_work_branch(self.planner,self.schedule,f'package:{self.composite.pk}',selected_items=[self.a.pk])
        entry=add_work_branch(self.planner,self.schedule,f'package:{self.composite.pk}')[0]
        self.assertEqual([item['id'] for item in entry.metadata['items']],[self.b.pk])
        self.assertTrue(add_entry(self.admin,self.schedule,{'kind':'work','work':self.simple}))

    def test_existing_gpr_rows_can_be_edited_but_cannot_gain_restricted_components(self):
        row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=JAN,quantity=1)
        payload={'work':self.simple.pk,'month':JAN.isoformat(),'quantity':'2','load_profile':''}
        form=WorkAllocationForm(payload,instance=row,user=self.planner,version=self.version,month=JAN)
        self.assertTrue(form.is_valid(),form.errors);form.save();row.refresh_from_db();self.assertEqual(row.quantity,2)
        row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.composite,month=JAN,selected_item_ids=[self.b.pk])
        form=WorkAllocationForm({**payload,'work':self.composite.pk,'selected_item_ids':'[]'},instance=row,user=self.planner,version=self.version,month=JAN)
        self.assertFalse(form.is_valid())

    def test_excel_cannot_add_legacy_work_but_can_update_previously_saved_row(self):
        from .workspace_excel import make_workbook,import_workbook
        from django.core.files.uploadedfile import SimpleUploadedFile
        row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=JAN,quantity=1)
        data=make_workbook(self.version,[JAN])
        upload=lambda:SimpleUploadedFile('plan.xlsx',data)
        row.delete()
        with self.assertRaises(ValidationError):import_workbook(self.planner,self.version,upload(),[JAN])
        self.assertFalse(self.version.work_allocations.exists())
        import_workbook(self.admin,self.version,upload(),[JAN])
        import_workbook(self.planner,self.version,upload(),[JAN])
        self.assertEqual(self.version.work_allocations.get().quantity,1)

    def test_marked_ancestor_hides_descendants_and_blocks_direct_selection(self):
        ProjectWorkItem.objects.filter(pk=self.b.pk).update(parent=self.a)
        tokens=self.tokens(choices(self.works,user=self.planner))
        self.assertNotIn(f'item:{self.b.pk}',tokens)
        self.assertNotIn(f'package:{self.composite.pk}',tokens)
        with self.assertRaises(ValidationError):resolve(self.works,f'item:{self.b.pk}',user=self.planner)

    def test_editor_component_choices_keep_history_without_offering_new_legacy_items(self):
        from apps.works.planning_access import component_options
        options=component_options(self.works,[],self.planner)
        self.assertNotIn(str(self.simple.pk),options)
        self.assertEqual([item['id'] for item in options[str(self.composite.pk)]],[self.b.pk])
        row=WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.composite,month=JAN)
        options=component_options(self.works,[row],self.planner)
        self.assertEqual({item['id'] for item in options[str(self.composite.pk)]},{self.a.pk,self.b.pk})

    def test_monthly_plan_and_bulk_post_cannot_select_restricted_work(self):
        from .forms import MonthlyPlanForm
        for user,expected in [(self.planner,False),(self.admin,True)]:
            form=MonthlyPlanForm(user=user)
            self.assertEqual(form.fields['project_work'].queryset.filter(pk=self.simple.pk).exists(),expected)
        self.client.force_login(self.planner)
        url=reverse('planning:workspace_bulk_add',args=[self.version.pk,'works'])
        response=self.client.post(url,{'items':[self.simple.pk],'months':[JAN.isoformat()]})
        self.assertEqual(response.status_code,400)
        self.assertFalse(self.version.work_allocations.exists())

    def test_meeting_import_cannot_bypass_marker_by_automatic_name_matching(self):
        from .meeting_import import apply_meeting_import
        sheet={'name':self.obj.name,'entries':[{'kind':'work','name':self.simple.name,'unit':self.simple.unit,'quantity':'1','month':JAN.isoformat(),'section':self.simple.section.name,'row':1}], 'errors':[],'warnings':[]}
        with self.assertRaises(ValidationError):
            apply_meeting_import(self.planner,self.obj.project_id,[sheet],{'0'},JAN,date(2026,3,31),target_version=self.version,allow_parent_quantity=True)
        self.assertFalse(self.version.work_allocations.exists())
        apply_meeting_import(self.admin,self.obj.project_id,[sheet],{'0'},JAN,date(2026,3,31),target_version=self.version,allow_parent_quantity=True)
        self.assertTrue(self.version.work_allocations.filter(work=self.simple).exists())
