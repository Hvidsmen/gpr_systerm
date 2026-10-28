from io import BytesIO
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
from apps.accounts.models import Company,Role,User
from .models import Equipment,EquipmentType,Brigade,EquipmentCrewPosition
from .fleet_import import HEADERS,read_rows,inspect,apply,fingerprint
from .equipment_levels import gpr_types,smg_types


def workbook(rows):
    book=Workbook();sheet=book.active;sheet.title='Сводка';sheet.append(HEADERS);sheet.append(list(range(1,10)))
    for row in rows:sheet.append(row)
    stream=BytesIO();book.save(stream);stream.seek(0);return stream

ROW=[1,2021,'КамАЗ 6520','INV-1','А001АА','VIN-1','Дудинка','Самосвалы','Водитель автомобиля.']

class FleetImportTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Fleet')
        role,_=Role.objects.get_or_create(code='ADMIN',defaults={'name':'Admin'})
        self.user=User.objects.create_user(username='fleet-import',company=self.company,role=role)
        self.client.force_login(self.user)
        self.rows=read_rows(workbook([ROW]))
        self.url=reverse('resources:fleet_import')

    def test_import_shared_position_hierarchy_and_repeated_import(self):
        report=apply(self.user,self.rows,fingerprint(self.company))
        self.assertEqual(report['new_units'],1)
        unit=Equipment.objects.get(company=self.company)
        self.assertEqual(unit.manufacture_year,2021)
        self.assertEqual(unit.inventory_number,'INV-1')
        self.assertEqual(unit.operator_position.planning_mapping.brigade.name,'Водитель автомобиля')
        self.assertEqual(unit.type.name,'Самосвалы / КамАЗ 6520')
        self.assertEqual(unit.type.parent.name,'Самосвалы')
        self.assertEqual(gpr_types(EquipmentType.objects.all()).get().pk,unit.type.parent_id)
        self.assertEqual(smg_types(EquipmentType.objects.all()).get().pk,unit.type_id)
        self.assertEqual(EquipmentCrewPosition.objects.filter(equipment_type=unit.type).get().count,1)
        self.assertEqual(apply(self.user,self.rows,fingerprint(self.company))['new_units'],0)
        self.assertEqual(Equipment.objects.count(),1);self.assertEqual(Brigade.objects.count(),1)

    def test_duplicate_vin_keeps_different_machines_and_blank_plates(self):
        other=list(ROW);other[3]='INV-2';other[4]=''
        first=list(ROW);first[4]=''
        rows=read_rows(workbook([first,other]))
        self.assertTrue(inspect(self.company,rows)['warnings'])
        apply(self.user,rows,fingerprint(self.company))
        self.assertEqual(Equipment.objects.filter(plate_number='').count(),2)

    def test_conflicting_data_and_stale_preview_roll_back(self):
        signature=fingerprint(self.company)
        Brigade.objects.create(company=self.company,name='Новая должность')
        with self.assertRaisesMessage(ValidationError,'Справочники изменились'):apply(self.user,self.rows,signature)
        self.assertFalse(Equipment.objects.exists())
        apply(self.user,self.rows,fingerprint(self.company))
        changed=[{**self.rows[0],'vin':'OTHER'}]
        with self.assertRaisesMessage(ValidationError,'другими реквизитами'):inspect(self.company,changed)

    def test_ambiguous_operator_roles_are_not_added_as_two_person_crew(self):
        other=list(ROW);other[3]='INV-2';other[4]='А002АА';other[8]='Машинист'
        rows=read_rows(workbook([ROW,other]))
        apply(self.user,rows,fingerprint(self.company))
        self.assertEqual(Brigade.objects.count(),2)
        self.assertEqual(Equipment.objects.count(),2)
        self.assertFalse(EquipmentCrewPosition.objects.exists())

    def test_retired_fleet_import_cannot_create_positions_or_machines(self):
        response=self.client.post(self.url,{'file':SimpleUploadedFile('Техника.xlsm',workbook([ROW]).read())})
        self.assertEqual(response.status_code,410);self.assertFalse(Equipment.objects.exists());self.assertFalse(Brigade.objects.exists())

    def test_duplicate_identifiers_are_blocked_and_other_company_stays_separate(self):
        with self.assertRaises(ValidationError):read_rows(workbook([ROW,ROW]))
        other=Company.objects.create(name='Other')
        EquipmentType.objects.create(company=other,name='Самосвалы')
        self.assertEqual(inspect(self.company,self.rows)['new_types'],2)
        role,_=Role.objects.get_or_create(code='OBSERVER',defaults={'name':'Observer'})
        self.user.role=role;self.user.save()
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_unit_category_selector_omits_model_and_preserves_existing_link(self):
        from .equipment_units import UnitForm
        apply(self.user,self.rows,fingerprint(self.company))
        unit=Equipment.objects.get(company=self.company)
        variant_id=unit.type_id
        form=UnitForm(instance=unit,company=self.company)
        self.assertEqual(form.initial['type'],unit.type.parent_id)
        self.assertEqual(list(form.fields['type'].queryset.values_list('name',flat=True)),['Самосвалы'])
        data={'type':unit.type.parent_id,'name':unit.name,'brand_model':unit.brand_model,
              'inventory_number':unit.inventory_number,'plate_number':unit.plate_number,
              'vin':unit.vin,'is_active':'on'}
        form=UnitForm(data,instance=unit,company=self.company)
        self.assertTrue(form.is_valid(),form.errors)
        saved=form.save()
        self.assertEqual(saved.type_id,variant_id)
        self.assertEqual(saved.brand_model,'КамАЗ 6520')


class EquipmentPlanningLevelsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from apps.planning.test_workspace import WorkspaceTests
        WorkspaceTests.setUpTestData.__func__(cls)

    def test_gpr_category_and_smg_model_choices(self):
        from datetime import date
        from apps.planning.workspace_services import WorkspaceService
        from apps.planning.workspace_forms import ResourceAllocationForm
        from apps.planning.daily_schedule_table_forms import AddEntryForm
        from apps.planning.models import DailySchedule
        variant=EquipmentType.objects.create(company=self.company,parent=self.equipment,name='Excavator / Model',brand_model='Model')
        workspace=WorkspaceService.create(self.planner,self.obj,'Types',date(2026,1,1),date(2026,1,31))
        form=ResourceAllocationForm(user=self.planner,version=workspace.baseline_version,month=date(2026,1,1))
        self.assertIn(self.equipment,form.fields['equipment_type'].queryset)
        self.assertNotIn(variant,form.fields['equipment_type'].queryset)
        schedule=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=date(2026,1,1),revision=1,created_by=self.planner)
        field=AddEntryForm(schedule=schedule).fields['equipment']
        self.assertNotIn(variant,field.queryset)
        self.assertIn(self.equipment,field.queryset)

