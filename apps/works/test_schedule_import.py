from io import BytesIO
from decimal import Decimal
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject, Section
from apps.planning.models import LoadProfile
from .models import ProjectWork, ProjectWorkItem, ComplexObject
from .schedule_import import HEADERS, parse_schedule, apply_rows, ImportForm


def workbook(quantity=290):
    book=Workbook();sheet=book.active
    sheet.append(['Пояснение к графику'])
    sheet.append([])
    sheet.append(HEADERS+['Факт с начала строительства','Остаток работ','План /факт',1])
    sheet.append(['УЗА 26','Видеонаблюдение','210-СВН','3.6','Кабель','м.п.',quantity,0,0,'План',99])
    sheet.append(['УЗА 26','Видеонаблюдение',None,None,None,None,None,120,170,'Факт',10])
    for column in range(3,8):sheet.merge_cells(start_row=4,start_column=column,end_row=5,end_column=column)
    sheet.append(['УЗА 26','Видеонаблюдение','210-СВН','3.6','Оборудование','шт',5,0,5,'План'])
    stream=BytesIO();book.save(stream);stream.seek(0)
    return stream

class ScheduleParserTests(SimpleTestCase):
    def test_merged_cells_and_fact_rows_do_not_duplicate_work(self):
        rows,ignored=parse_schedule(workbook())
        self.assertEqual(len(rows),2);self.assertEqual(ignored,1)
        self.assertEqual(rows[0]['quantity'],'290.000')
        self.assertEqual(rows[0]['rd_section'],'210-СВН')
        self.assertNotIn('daily',rows[0]);self.assertNotIn('fact',rows[0])

    def test_invalid_norms_and_formulas_are_rejected(self):
        for quantity in (0,-1,'NaN','1.1234','=1+1','ошибка'):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):parse_schedule(workbook(quantity))

class ScheduleImportTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Импорт графика')
        self.user=User.objects.create_user(username='importer',company=self.company,role=Role.objects.get(code='ADMIN'))
        project=Project.objects.create(company=self.company,name='Ямал')
        self.obj=ConstructionObject.objects.create(company=self.company,project=project,name='КТП')
        self.section=Section.objects.create(company=self.company,construction_object=self.obj,name='Общие работы')
        self.profile=LoadProfile.objects.create(company=self.company,name='Равномерный')
        self.rows,_=parse_schedule(workbook())
        self.client.force_login(self.user);self.url=reverse('works:schedule_import')

    def test_import_preserves_hierarchy_and_norms_and_repeat_is_idempotent(self):
        self.assertEqual(apply_rows(self.user,self.section,self.profile,self.rows),2)
        package=ProjectWork.objects.get(section=self.section)
        self.assertEqual(package.unit,'')
        self.assertEqual(package.complex_object.name,'УЗА 26')
        self.assertEqual((package.rd_section,package.rdc_point),('210-СВН','3.6'))
        self.assertEqual(package.items.get(name='Кабель').quantity_per_unit,Decimal(290))
        self.assertEqual(package.items.get(name='Кабель').planned_quantity,0)
        self.assertEqual(package.items.get(name='Кабель').load_profile,self.profile)
        self.assertEqual(apply_rows(self.user,self.section,self.profile,self.rows),0)
        self.assertEqual(ProjectWork.objects.count(),1);self.assertEqual(ProjectWorkItem.objects.count(),2)

    def test_conflict_never_overwrites_existing_norm(self):
        apply_rows(self.user,self.section,self.profile,self.rows)
        self.rows[0]['quantity']='500'
        with self.assertRaises(ValidationError):apply_rows(self.user,self.section,self.profile,self.rows)
        self.assertEqual(ProjectWorkItem.objects.get(name='Кабель').quantity_per_unit,Decimal(290))

    def test_item_failure_rolls_back_entire_import(self):
        with patch.object(ProjectWorkItem,'full_clean',side_effect=ValidationError('Ошибка')):
            with self.assertRaises(ValidationError):apply_rows(self.user,self.section,self.profile,self.rows)
        self.assertFalse(ComplexObject.objects.exists());self.assertFalse(ProjectWork.objects.exists())

    def preview(self):
        return self.client.post(self.url,{'construction_object':self.obj.pk,'section':self.section.pk,'load_profile':self.profile.pk,'file':SimpleUploadedFile('График.xlsx',workbook().read())})

    def test_preview_then_confirmation_and_replay(self):
        response=self.preview()
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Предпросмотр')
        self.assertFalse(ProjectWork.objects.exists())
        token=response.context['token']
        self.assertEqual(self.client.post(self.url,{'action':'confirm','token':token}).status_code,302)
        self.assertEqual(ProjectWorkItem.objects.count(),2)
        self.assertEqual(self.client.post(self.url,{'action':'confirm','token':token}).status_code,302)
        self.assertEqual(ProjectWorkItem.objects.count(),2)

    def test_imported_package_can_be_edited_before_setting_its_unit(self):
        from .forms import ProjectWorkForm
        apply_rows(self.user,self.section,self.profile,self.rows)
        work=ProjectWork.objects.get(section=self.section)
        form=ProjectWorkForm({'name':work.name,'section':self.section.pk,'complex_object':work.complex_object_id,'unit':'','kind':'COMPOSITE','status':'PLANNED','rd_section':work.rd_section,'rdc_point':work.rdc_point},user=self.user,fixed_object=self.obj,instance=work)
        self.assertTrue(form.is_valid(),form.errors)

    def test_tampered_token_cannot_write(self):
        response=self.client.post(self.url,{'action':'confirm','token':'wrong'})
        self.assertContains(response,'недействителен')
        self.assertFalse(ProjectWork.objects.exists())

    def test_company_and_assignment_scope_rechecked_on_confirmation(self):
        token=self.preview().context['token']
        self.user.role=Role.objects.get(code='PLANNER');self.user.save()
        form=ImportForm(user=self.user)
        self.assertFalse(form.fields['section'].queryset.exists())
        response=self.client.post(self.url,{'action':'confirm','token':token})
        self.assertContains(response,'больше недоступен')
        self.assertFalse(ProjectWork.objects.exists())

    def test_observer_cannot_import(self):
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save()
        self.assertEqual(self.client.get(self.url).status_code,403)

    def test_import_sections_filtered_by_selected_object(self):
        other=ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Другой объект')
        section=Section.objects.create(company=self.company,construction_object=other,name='Чужой раздел')
        form=ImportForm({'construction_object':self.obj.pk,'section':section.pk},user=self.user)
        self.assertEqual(list(form.fields['section'].queryset),[self.section])
        self.assertFalse(form.is_valid())
        self.assertIn('section',form.errors)
        self.assertEqual(ImportForm(user=self.user).fields['section'].queryset.count(),0)
