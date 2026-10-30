from io import BytesIO
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from apps.accounts.models import Company,User,Role
from apps.audit.models import AuditLog
from .models import EquipmentType,EquipmentCategory,Equipment
from . import equipment_catalogue_groups as service
from .equipment_group_import import read_rows

HEADERS=['Название','Техника для планирования','Группа техники','Единица измерения','Активна','Категории водителя','Классы водителя']

class CatalogueGroupUpdateTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Учебный справочник групп')
        self.user=User.objects.create_user(username='group-file-editor',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.a=EquipmentCategory.objects.create(company=self.company,name='Старая группа')
        self.b=EquipmentCategory.objects.create(company=self.company,name='Вспомогательная техника')
        self.root=EquipmentType.objects.create(company=self.company,name='АПС',category=self.a,unit='ед.',is_active=False)
        self.model=EquipmentType.objects.create(company=self.company,name='АПС / Учебная модель',parent=self.root,unit='шт')
        self.unlisted=EquipmentType.objects.create(company=self.company,name='АПС / Вторая модель',parent=self.root)
        self.machine=Equipment.objects.create(company=self.company,type=self.model,name='Учебная машина',plate_number='TEST-001')
        self.direct=Equipment.objects.create(company=self.company,type=self.root,name='Учебная машина без модели')
        self.other=EquipmentType.objects.create(company=self.company,name='Кран',category=self.a)
        self.client.force_login(self.user);self.url=reverse('resources:equipment_group_import')
    def file(self,rows,headers=HEADERS):
        book=Workbook();sheet=book.active;sheet.title='Техника для планирования';sheet.append(headers)
        for row in rows:sheet.append(row)
        out=BytesIO();book.save(out);book.close()
        return SimpleUploadedFile('Учебный справочник.xlsx',out.getvalue())
    def parsed(self,group='Вспомогательная  техника'):
        rows,errors=read_rows(self.file([[self.model.name,self.root.name,group,'другая ед.','Да','B','1'],[self.root.name,self.root.name,group]]));self.assertFalse(errors);return rows
    def test_export_shape_is_recognized_without_numbered_machine_identifiers(self):
        rows=self.parsed();self.assertEqual(len(rows),2);self.assertEqual(rows[0]['kind'],'catalogue')
        response=self.client.post(self.url,{'file':self.file([[self.model.name,self.root.name,'Вспомогательная  техника']])})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Предпросмотр групп');self.assertContains(response,'Подтвердить обновление групп')
        self.root.refresh_from_db();self.machine.refresh_from_db();self.assertEqual(self.root.category_id,self.a.pk);self.assertEqual(self.machine.catalogue_group_id,self.a.pk)
        self.assertFalse(AuditLog.objects.exists())
    def test_confirm_updates_root_all_models_and_machines_but_no_other_attributes(self):
        rows=self.parsed();report=service.preview(self.user,rows)
        self.assertEqual((report['positions'],report['models'],report['machines']),(1,2,2))
        signed=service.token(self.user,rows,report);result=service.confirm(self.user,signed);self.assertEqual(result['updated'],1)
        for model in [self.root,self.model,self.unlisted]:model.refresh_from_db();self.assertEqual(model.category_id,self.b.pk)
        for machine in [self.machine,self.direct]:machine.refresh_from_db();self.assertEqual(machine.catalogue_group_id,self.b.pk)
        self.assertEqual(self.model.parent_id,self.root.pk);self.assertEqual(self.model.unit,'шт');self.assertFalse(self.root.is_active);self.assertEqual(self.machine.type_id,self.model.pk)
        self.other.refresh_from_db();self.assertEqual(self.other.category_id,self.a.pk)
        self.assertTrue(AuditLog.objects.filter(user=self.user,entity_type='resources.EquipmentType',entity_id=str(self.root.pk)).exists())
        with self.assertRaises(ValidationError):service.confirm(self.user,signed)
    def test_http_confirm_requires_preview_and_is_bound_to_user_and_company(self):
        response=self.client.post(self.url,{'file':self.file([[self.root.name,self.root.name,self.b.name]])});signed=response.context['token'];self.assertTrue(signed)
        colleague=User.objects.create_user(username='other-editor',company=self.company,role=self.user.role)
        self.client.force_login(colleague);self.assertEqual(self.client.post(self.url,{'action':'confirm_catalogue','token':signed}).status_code,400)
        foreign=User.objects.create_user(username='foreign-editor',company=Company.objects.create(name='Чужая компания'),role=self.user.role)
        self.client.force_login(foreign);self.assertEqual(self.client.post(self.url,{'action':'confirm_catalogue','token':signed}).status_code,400)
        self.client.force_login(self.user);self.assertEqual(self.client.post(self.url,{'action':'confirm_catalogue','token':signed+'x'}).status_code,400)
        self.assertEqual(self.client.post(self.url,{'action':'confirm_catalogue','token':signed}).status_code,200);self.root.refresh_from_db();self.assertEqual(self.root.category_id,self.b.pk)
    def test_conflicting_groups_reject_entire_file_before_mutating(self):
        upload=self.file([[self.root.name,self.root.name,self.b.name],[self.model.name,self.root.name,'Другая группа']])
        response=self.client.post(self.url,{'file':upload});self.assertEqual(response.status_code,400);self.assertContains(response,'разные группы',status_code=400)
        self.root.refresh_from_db();self.assertEqual(self.root.category_id,self.a.pk);self.assertFalse(EquipmentCategory.objects.filter(name='Другая группа').exists())
    def test_stale_model_or_machine_membership_requires_new_preview(self):
        rows=self.parsed();report=service.preview(self.user,rows);signed=service.token(self.user,rows,report)
        self.machine.type=self.other;self.machine.save(update_fields=['type'])
        with self.assertRaisesMessage(ValidationError,'изменился после предпросмотра'):service.confirm(self.user,signed)
        self.root.refresh_from_db();self.assertEqual(self.root.category_id,self.a.pk)
    def test_new_group_is_created_once_only_after_confirmation(self):
        rows=self.parsed('Новая группа');report=service.preview(self.user,rows)
        self.assertEqual(report['new_groups'],['Новая группа']);self.assertFalse(EquipmentCategory.objects.filter(name='Новая группа').exists())
        result=service.confirm(self.user,service.token(self.user,rows,report));self.assertEqual(result['created_groups'],1)
        self.assertEqual(EquipmentCategory.objects.filter(company=self.company,name='Новая группа').count(),1)
    def test_blank_preserves_and_explicit_no_group_clears_children(self):
        rows=self.parsed('');report=service.preview(self.user,rows);self.assertFalse(report['entries']);self.assertEqual(report['blank'],2)
        rows=self.parsed('Без группы');report=service.preview(self.user,rows);service.confirm(self.user,service.token(self.user,rows,report))
        self.model.refresh_from_db();self.machine.refresh_from_db();self.assertIsNone(self.model.category_id);self.assertIsNone(self.machine.catalogue_group_id)
    def test_legacy_child_override_is_repaired_when_root_group_is_unchanged(self):
        EquipmentType.objects.filter(pk=self.model.pk).update(category=self.b);Equipment.objects.filter(pk=self.machine.pk).update(catalogue_group=self.b)
        rows=self.parsed(self.a.name);report=service.preview(self.user,rows);self.assertEqual((len(report['entries']),report['models'],report['machines']),(1,1,1))
        service.confirm(self.user,service.token(self.user,rows,report));self.model.refresh_from_db();self.machine.refresh_from_db();self.assertEqual(self.model.category_id,self.a.pk);self.assertEqual(self.machine.catalogue_group_id,self.a.pk)
    def test_unknown_names_are_visible_and_foreign_records_do_not_match(self):
        foreign=Company.objects.create(name='Другой справочник');EquipmentType.objects.create(company=foreign,name='Чужой тип')
        rows,errors=read_rows(self.file([['Чужой тип','Чужой тип',self.b.name],[self.root.name,self.root.name,self.b.name]]));self.assertFalse(errors)
        report=service.preview(self.user,rows);self.assertEqual(len(report['warnings']),1);self.assertIn('Чужой тип',report['warnings'][0]);self.assertEqual(report['positions'],1)
    def test_ambiguous_group_and_incorrect_model_parent_cancel_preview(self):
        EquipmentCategory.objects.create(company=self.company,name='Вспомогательная  техника')
        with self.assertRaisesMessage(ValidationError,'несколько групп'):service.preview(self.user,self.parsed())
        rows,errors=read_rows(self.file([[self.model.name,self.other.name,self.a.name]]));self.assertFalse(errors)
        with self.assertRaisesMessage(ValidationError,'не относится'):service.preview(self.user,rows)
    def test_formulas_and_duplicate_headers_rejected_while_ignored_columns_stay_untouched(self):
        for upload in [self.file([[self.root.name,self.root.name,'=1+1']]),self.file([[self.root.name,self.a.name,self.b.name]],['Название','Группа техники','Группа техники'])]:
            rows,errors=read_rows(upload);self.assertTrue(errors)
        rows,errors=read_rows(self.file([[self.root.name,self.root.name,self.b.name,'=1+1']]))
        self.assertFalse(errors);self.assertEqual(len(rows),1)
    def test_four_column_template_is_supported_and_read_only_role_is_denied(self):
        rows,errors=read_rows(self.file([[self.root.name,self.b.name,'ед.','Да']],['Название','Группа техники','Единица измерения','Активна']))
        self.assertFalse(errors);self.assertEqual(service.preview(self.user,rows)['positions'],1)
        actor=User.objects.create_user(username='read-only-mechanization',company=self.company,role=Role.objects.get(code='MECHANIZATION_DISPATCHER'))
        self.client.force_login(actor);self.assertEqual(self.client.get(self.url).status_code,403);self.assertEqual(self.client.post(self.url,{'file':self.file([[self.root.name,self.root.name,self.b.name]])}).status_code,403)
