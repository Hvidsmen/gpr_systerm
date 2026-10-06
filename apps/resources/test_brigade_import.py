from io import BytesIO
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook, load_workbook
from apps.accounts.models import Company, User, Role
from .models import Brigade, BrigadeGroup, BrigadeMacroGroup
from .brigade_import import HEADERS


class BrigadeImportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Brigade Excel')
        self.user = User.objects.create_user(username='brigade-excel-planner', company=self.company, role=Role.objects.get(code='PLANNER'))
        self.client.force_login(self.user)
        self.url = reverse('resources:brigade_import')

    def file(self, rows, headers=HEADERS):
        workbook = Workbook()
        workbook.active.append(headers)
        for row in rows:
            workbook.active.append(row)
        output = BytesIO()
        workbook.save(output)
        workbook.close()
        return SimpleUploadedFile('brigades.xlsx', output.getvalue())

    def test_import_catalogs_defaults_duplicates_and_company_scope(self):
        other = Company.objects.create(name='Other brigade Excel')
        foreign = BrigadeGroup.objects.create(company=other,name='Монтаж')
        response = self.client.post(self.url, {'file':self.file([
            ['Бригада А','Монтаж','Строительство','Описание',''],
            ['Бригада Б','монтаж','строительство','','Нет'],
            ['бригада а','Unused','Unused','','Да'],
        ])})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result'], {'created':2,'skipped':1,'groups':1,'macros':1})
        first = Brigade.objects.get(company=self.company,name='Бригада А')
        second = Brigade.objects.get(company=self.company,name='Бригада Б')
        self.assertEqual(first.code,'BR-000001')
        self.assertEqual(first.unit, 'чел.')
        self.assertTrue(first.is_active)
        self.assertFalse(second.is_active)
        self.assertEqual(first.description,'Описание')
        self.assertEqual(first.group_id,second.group_id)
        self.assertEqual(first.macro_group_id,second.macro_group_id)
        self.assertNotEqual(first.group_id,foreign.pk)
        self.assertEqual(self.client.post(self.url,{'file':self.file([['Бригада А','','','Changed','Нет']])}).context['result']['created'],0)
        first.refresh_from_db()
        self.assertEqual(first.description,'Описание')
        self.assertTrue(first.is_active)

    def test_errors_cancel_import_and_template(self):
        for rows in ([['Valid','New'],['','Other']], [['=1+1']], [['Bad','','','','Wrong']]):
            self.assertEqual(self.client.post(self.url,{'file':self.file(rows)}).status_code,400)
            self.assertFalse(Brigade.objects.exists())
            self.assertFalse(BrigadeGroup.objects.exists())
            self.assertFalse(BrigadeMacroGroup.objects.exists())
        response = self.client.get(reverse('resources:brigade_import_template'))
        workbook = load_workbook(BytesIO(response.content))
        self.assertEqual(list(next(workbook.worksheets[0].values)),HEADERS)
        workbook.close()
        self.assertEqual(self.client.post(self.url,{'file':self.file([['Only name']],headers=['Название'])}).status_code,200)
        self.assertIsNone(Brigade.objects.get().group)

    def test_bad_files_and_roles(self):
        for file in (SimpleUploadedFile('broken.xlsx',b'broken'),SimpleUploadedFile('old.xls',b'broken'),self.file([])):
            self.assertEqual(self.client.post(self.url,{'file':file}).status_code,400)
        for code in ('MANAGER','FOREMAN'):
            user = User.objects.create_user(username='brigade-excel-'+code,company=self.company,role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code,403)
            self.assertEqual(self.client.post(self.url,{'file':self.file([['Unauthorized']])}).status_code,403)
            self.assertEqual(self.client.get(reverse('resources:brigade_import_template')).status_code,403)
