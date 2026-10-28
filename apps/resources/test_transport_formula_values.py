from io import BytesIO
from zipfile import ZipFile,ZIP_DEFLATED
from xml.etree import ElementTree as ET
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.urls import reverse
from openpyxl import Workbook
from apps.production import test_transport as transport_fixtures
from . import transport_import as service
from . import test_transport_import as fixtures
from .models import Equipment
from .transport_import_models import TransportImportRow

NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'


def formula_upload(rows,caches=None,extra=None):
    book=Workbook();sheet=book.active;sheet.title='Сводка';sheet.append(service.HEADERS);sheet.append(list(range(1,22)))
    for row in rows:sheet.append(row)
    for coordinate,value in (extra or {}).items():sheet[coordinate]=value
    stream=BytesIO();book.save(stream)
    result=BytesIO()
    with ZipFile(BytesIO(stream.getvalue())) as source,ZipFile(result,'w',ZIP_DEFLATED) as target:
        for entry in source.infolist():
            data=source.read(entry.filename)
            if entry.filename=='xl/worksheets/sheet1.xml' and caches:
                root=ET.fromstring(data)
                for cell in root.findall('.//{'+NS+'}c'):
                    if cell.get('r') not in caches:continue
                    value=caches[cell.get('r')];cached=cell.find('{'+NS+'}v')
                    if cached is None:cached=ET.SubElement(cell,'{'+NS+'}v')
                    if isinstance(value,bool):cell.set('t','b');value=1 if value else 0
                    elif isinstance(value,str):cell.set('t','str')
                    else:cell.attrib.pop('t',None)
                    cached.text=str(value)
                data=ET.tostring(root)
            target.writestr(entry,data)
    return SimpleUploadedFile('Формулы.xlsm',result.getvalue())


class TransportFormulaValueTests(TestCase):
    @classmethod
    def setUpTestData(cls):transport_fixtures.TransportTests.setUpTestData.__func__(cls)
    row=fixtures.TransportImportTests.row
    def test_numbering_and_extra_formulas_never_block_machine_rows(self):
        file=formula_upload([self.row(number='=ROW()-2')],extra={'V3':'=SUM(A3:A4)'})
        batch=service.preview(self.admin,[file],self.day)
        self.assertEqual(batch.rows.get().status,'READY');service.apply(self.admin,batch)
        self.assertEqual(batch.rows.get().status,'APPLIED')
        self.assertEqual(batch.rows.get().original['№'],'=ROW()-2')
    def test_cached_excel_results_are_loaded_while_original_formulas_remain_auditable(self):
        file=formula_upload([self.row(number='=ROW()-2',model='="CAT 320"',site='="'+self.camp.name+'"',in_gpr='=TRUE()',comment='=""')],{'A3':1,'C3':'CAT 320','G3':self.camp.name,'K3':True,'U3':''})
        batch=service.preview(self.admin,[file],self.day);row=batch.rows.get()
        self.assertEqual(row.status,'READY');self.assertEqual(row.data['model'],'CAT 320');self.assertEqual(row.data['number'],'1')
        self.assertEqual(row.data['comment'],'');self.assertEqual(row.data['in_gpr'],'Да');self.assertEqual(row.original['Марка/модель'],'="CAT 320"')
        service.apply(self.admin,batch);self.assertTrue(Equipment.objects.filter(inventory_number='INV-100').exists())
    def test_missing_result_is_reported_for_the_actual_cell_without_importing_formula_as_a_vin(self):
        batch=service.preview(self.admin,[formula_upload([self.row(vin='=B10')])],self.day)
        row=batch.rows.get();self.assertEqual(row.status,'PENDING');self.assertIn('F3',row.error);self.assertIn('не сохранён результат',row.error)
        service.apply(self.admin,batch);self.assertFalse(Equipment.objects.filter(vin='=B10').exists())
        self.assertEqual(service.retry_pending(self.admin),0)
        row.refresh_from_db();self.assertIn('F3',row.error)
    def test_old_formula_number_errors_retry_without_reupload_and_same_batch_vin_repeats_stay_pending(self):
        batch=service.preview(self.admin,[fixtures.upload([self.row(),self.row()])],self.day)
        for row in batch.rows.all():
            row.status='PENDING';row.error='Формулы не загружаются как реквизиты. Замените их значениями.'
            row.data['number']='=A3+1';row.original['№']='=A3+1';row.save()
        batch.applied_at=timezone.now();batch.save()
        original=batch.rows.first().original.copy();self.client.force_login(self.operator)
        response=self.client.post(reverse('resources:transport_import_errors'))
        self.assertEqual(response.status_code,302);self.assertEqual(batch.rows.filter(status='APPLIED').count(),1)
        self.assertEqual(batch.rows.filter(status='PENDING').count(),1);self.assertEqual(Equipment.objects.filter(vin='VIN-100').count(),1)
        self.assertEqual(batch.rows.get(status='APPLIED').original,original)
    def test_old_unresolved_optional_field_formula_still_requires_a_result(self):
        batch=service.preview(self.admin,[fixtures.upload([self.row()])],self.day);row=batch.rows.get()
        row.data['maintenance']='=VLOOKUP(B3,Other!A:D,4,0)';row.original[service.HEADERS[19]]=row.data['maintenance']
        row.original['Проведение ТО · другая подпись']=row.original.pop(service.HEADERS[19])
        row.error='Формулы не загружаются как реквизиты. Замените их значениями.';row.status='PENDING';row.save();batch.applied_at=timezone.now();batch.save()
        self.assertEqual(service.retry_pending(self.admin),0);row.refresh_from_db();self.assertIn('T3',row.error)
        self.assertEqual(service.retry_pending(self.admin),0);row.refresh_from_db();self.assertIn('T3',row.error)
        self.assertFalse(Equipment.objects.filter(vin='VIN-100').exists())
