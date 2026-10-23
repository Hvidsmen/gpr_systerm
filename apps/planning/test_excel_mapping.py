from copy import deepcopy
from datetime import date
from uuid import uuid4
from django.test import TestCase, SimpleTestCase
from django.core.exceptions import ValidationError
from apps.resources.models import Brigade,EquipmentType
from apps.works.models import ProjectWork,ProjectWorkItem
from core.excel_mapping import ExcelAliasesField
from . import test_workspace as fixtures
from .test_gpr_excel import classic
from .gpr_excel import parse_gpr
from .gpr_excel_views import apply_gpr
from .meeting_import import resolve_sheet,apply_meeting_import
from .models import PlanningWorkspace


class MappingFieldTests(SimpleTestCase):
    def test_lines_are_saved_as_multiple_aliases_and_round_trip(self):
        field=ExcelAliasesField()
        self.assertEqual(field.clean('  Машинист\n\nМашинист 6 разряда '),['Машинист','Машинист 6 разряда'])
        self.assertEqual(field.prepare_value(['A','B']),'A\nB')
        with self.assertRaises(ValidationError):field.clean('Экскаватор\n ЭКСКАВАТОР ')


class ExcelMappingTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)

    def setUp(self):
        from apps.accounts.models import User,Role
        self.importer=User.objects.create_user(username='mapping-resource-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.simple.excel_aliases=['Монтаж','Второе название'];self.simple.unit='м3';self.simple.save()
        self.brigade.excel_aliases=['Мастер','Старший мастер'];self.brigade.save()
        self.equipment.excel_aliases=['Экскаватор','Гусеничный экскаватор'];self.equipment.save()

    def counts(self):
        return tuple(model.objects.count() for model in [ProjectWork,ProjectWorkItem,Brigade,EquipmentType,PlanningWorkspace])

    def payload(self):return parse_gpr(classic(),'plan.xlsx')
    def dates(self,payload):return {f"month_{m['column']}":date.fromisoformat(m['month']) for m in payload['months']}
    def sheet(self,rows):return {'name':self.obj.name,'entries':rows,'warnings':[],'errors':[]}

    def test_two_excel_names_for_one_record_work_across_uploads_without_creation(self):
        first=self.payload();before=self.counts()[:4]
        one=apply_gpr(self.importer,self.obj,first,self.dates(first),str(uuid4()))
        second=deepcopy(first)
        names={'work':'Второе название','labor':'Старший мастер','equipment':'Гусеничный экскаватор'}
        for row in second['entries']:row['name']=names[row['kind']]
        two=apply_gpr(self.importer,self.obj,second,self.dates(second),str(uuid4()))
        self.assertEqual(before,self.counts()[:4])
        for workspace in [one,two]:
            self.assertEqual(set(workspace.baseline_version.work_allocations.values_list('work_id',flat=True)),{self.simple.pk})
            self.assertEqual(set(workspace.baseline_version.resource_allocations.filter(kind='labor').values_list('brigade_id',flat=True)),{self.brigade.pk})
            self.assertEqual(set(workspace.baseline_version.resource_allocations.filter(kind='equipment').values_list('equipment_type_id',flat=True)),{self.equipment.pk})

    def test_unknown_entries_of_each_kind_block_apply_without_catalog_or_plan_writes(self):
        for kind in ['work','labor','equipment']:
            payload=self.payload()
            for row in payload['entries']:
                if row['kind']==kind:row['name']='Неизвестная запись Excel'
            before=self.counts()
            with self.subTest(kind=kind),self.assertRaisesMessage(ValidationError,'нет соответствия'):
                apply_gpr(self.importer,self.obj,payload,self.dates(payload),str(uuid4()))
            self.assertEqual(before,self.counts())

    def test_unknown_meeting_rows_are_reported_together_and_cannot_create_catalog(self):
        rows=[{'kind':kind,'name':f'Нет {kind}','unit':'м3','section':'s','month':'2026-01-01','quantity':'1','row':i} for i,kind in enumerate(['work','labor','equipment'],1)]
        before=self.counts()
        with self.assertRaises(ValidationError) as error:
            apply_meeting_import(self.importer,self.obj.project_id,[self.sheet(rows)],{'0'},date(2026,1,1),date(2026,1,31))
        self.assertEqual(len(error.exception.messages),3)
        self.assertEqual(before,self.counts())

    def test_ambiguous_mapping_and_duplicate_mapped_rows_are_rejected(self):
        other=EquipmentType.objects.create(company=self.company,name='Другой вид',excel_aliases=['Экскаватор'])
        payload=self.payload()
        with self.assertRaisesMessage(ValidationError,'неоднозначное'):
            apply_gpr(self.importer,self.obj,payload,self.dates(payload),str(uuid4()))
        other.excel_aliases=[];other.save()
        row=next(r for r in payload['entries'] if r['kind']=='equipment')
        payload['entries'].append({**row,'name':'Гусеничный экскаватор','row':999})
        with self.assertRaisesMessage(ValidationError,'несколько строк'):
            apply_gpr(self.importer,self.obj,payload,self.dates(payload),str(uuid4()))
        self.assertFalse(PlanningWorkspace.objects.exists())

    def test_item_mapping_is_limited_to_object_and_preserves_unit_checks(self):
        self.a.excel_aliases=['Сборка узла'];self.a.save()
        row={'kind':'work','name':'Сборка узла','unit':self.a.unit,'section':'Другое имя раздела Excel','month':'2026-01-01','quantity':'2','row':7}
        _,resolved=resolve_sheet(self.company,self.obj.project,self.sheet([row]),require_all=False)
        self.assertEqual((resolved[0]['target_type'],resolved[0]['target_id']),('item',self.a.pk))
        with self.assertRaisesMessage(ValidationError,'нет соответствия'):
            resolve_sheet(self.company,self.obj.project,self.sheet([{**row,'unit':'неверная единица'}]),require_all=False)

    def test_template_import_accepts_alias_with_or_without_id_and_rejects_foreign_scope(self):
        from .workspace_excel import resolve
        for identifier in [None,self.equipment.pk]:
            self.assertEqual(resolve({self.equipment.pk:self.equipment},identifier,'Гусеничный экскаватор','Техника'),self.equipment)
        with self.assertRaises(ValidationError):
            resolve({},None,'Гусеничный экскаватор','Техника')
        with self.assertRaises(ValidationError):
            resolve({self.equipment.pk:self.equipment},self.equipment.pk,'Неизвестное имя','Техника')

    def test_mapping_does_not_escape_construction_object(self):
        from apps.projects.models import ConstructionObject,Section
        obj=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой объект')
        section=Section.objects.create(company=self.company,construction_object=obj,name='s')
        work=ProjectWork.objects.create(company=self.company,section=section,name='Чужая работа',unit='м3',excel_aliases=['Только другой объект'])
        payload=self.payload()
        for row in payload['entries']:
            if row['kind']=='work':row['name']='Только другой объект'
        with self.assertRaisesMessage(ValidationError,'нет соответствия'):
            apply_gpr(self.importer,self.obj,payload,self.dates(payload),str(uuid4()))
        self.assertFalse(PlanningWorkspace.objects.exists())

    def test_catalog_form_saves_multiple_names_on_same_record(self):
        from apps.resources.forms import BrigadeForm
        form=BrigadeForm({'name':self.brigade.name,'unit':'чел.','is_active':'on','excel_aliases':'Мастер\nПрораб участка'},instance=self.brigade)
        self.assertTrue(form.is_valid(),form.errors)
        saved=form.save()
        self.assertEqual(saved.pk,self.brigade.pk)
        saved.refresh_from_db()
        self.assertEqual(saved.excel_aliases,['Мастер','Прораб участка'])
