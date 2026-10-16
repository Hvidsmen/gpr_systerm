from io import BytesIO
from django.test import SimpleTestCase, TestCase
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from openpyxl import Workbook, load_workbook
from apps.accounts.models import Company, Role, User
from .excel import parse, export
from .models import EstimateDocument, EstimateCatalog


def sample():
    w=Workbook();s=w.active
    s['A1']='ЛОКАЛЬНЫЙ СМЕТНЫЙ РАСЧЕТ';s['B3']='Обоснование';s['C3']='Наименование работ и затрат'
    s['A7']='Раздел 1. Монтаж'
    for r,a,b,c,u,q,base,cur in [(8,'1','ФЕР','Работа','м',2,None,None),(9,None,'91.1','Кран','маш.час',3,10,None),(10,None,None,'Всего по позиции',None,None,20,100),(11,None,None,'Итого по разделу 1',None,None,20,100),(12,None,'91.1','Повторная ведомость',None,None,10,50),(13,None,None,'ВСЕГО по смете',None,None,20,100)]:
        for col,val in [(1,a),(2,b),(3,c),(6,u),(9,q),(12,base),(14,cur)]:s.cell(r,col,val)
    out=BytesIO();w.save(out);return out.getvalue()


class ParserTests(SimpleTestCase):
    def test_totals_and_resources_are_not_double_counted(self):
        d=parse(sample(),'sample.xlsx')
        self.assertEqual(d['current_total'],'100')
        self.assertEqual([r['kind'] for r in d['rows']],['section','item','resource','detail','total','appendix','total'])
        self.assertEqual(d['rows'][2]['parent_row'],8)
        w=load_workbook(BytesIO(export([d])),data_only=False)
        self.assertIsNone(w['Свод смет']['D2'].value)
        self.assertEqual(w['Ресурсы'].max_row,2)
        self.assertEqual(w['Позиции'].max_row,2)
    def test_unknown_format(self):
        with self.assertRaises(ValueError):parse(b'not excel')


class AccessTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Estimates')
        self.role=Role.objects.get_or_create(code='PLANNER',defaults={'name':'Planner'})[0]
        self.user=User.objects.create_user(username='estimate-planner',company=self.company,role=self.role)
        self.client.force_login(self.user)
    def test_import_duplicate_and_scope(self):
        url=reverse('estimates:estimate_import')
        for _ in range(2):
            response=self.client.post(url,{'object_name':'Independent object','file':SimpleUploadedFile('sample.xlsx',sample())})
            self.assertEqual(response.status_code,302)
        self.assertEqual(EstimateDocument.objects.count(),1)
        self.assertEqual(EstimateCatalog.objects.count(),4)
        self.assertEqual(self.client.get(response.url).status_code,200)
        self.assertEqual(self.client.get(reverse('estimates:estimate_export')).status_code,200)
        other=Company.objects.create(name='Other');self.user.company=other;self.user.save()
        self.assertEqual(self.client.get(response.url).status_code,404)
    def test_observer_read_only(self):
        self.user.role=Role.objects.get_or_create(code='OBSERVER',defaults={'name':'Observer'})[0];self.user.save()
        self.assertEqual(self.client.get(reverse('estimates:estimate_list')).status_code,200)
        self.assertEqual(self.client.get(reverse('estimates:catalog_list')).status_code,200)
        self.assertEqual(self.client.post(reverse('estimates:estimate_import')).status_code,403)

    def test_upload_button_and_multiple_selection(self):
        response=self.client.get(reverse('estimates:estimate_list'))
        self.assertContains(response,'Загрузить сметы (несколько файлов)')
        self.assertContains(self.client.get(reverse('estimates:estimate_import')),' multiple')
        from openpyxl import load_workbook
        second=load_workbook(BytesIO(sample()));second.active['C8']='Другая работа';out=BytesIO();second.save(out)
        response=self.client.post(reverse('estimates:estimate_import'),{'object_name':'Batch','file':[SimpleUploadedFile('one.xlsx',sample()),SimpleUploadedFile('two.xlsx',out.getvalue()),SimpleUploadedFile('repeat.xlsx',sample())]})
        self.assertRedirects(response,reverse('estimates:estimate_list'))
        self.assertEqual(EstimateDocument.objects.count(),2)

    def test_invalid_batch_saves_nothing_and_names_bad_file(self):
        response=self.client.post(reverse('estimates:estimate_import'),{'object_name':'Batch','file':[SimpleUploadedFile('one.xlsx',sample()),SimpleUploadedFile('broken.xlsx',b'broken')]})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'broken.xlsx')
        self.assertEqual(EstimateDocument.objects.count(),0)

    def test_empty_batch_and_read_only_explanation(self):
        response=self.client.post(reverse('estimates:estimate_import'),{'object_name':'Batch'})
        self.assertEqual(response.status_code,200)
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save()
        response=self.client.get(reverse('estimates:estimate_list'))
        self.assertContains(response,'Ваша роль разрешает просмотр')
        self.assertNotContains(response,'Загрузить сметы (несколько файлов)')


class CalculationTests(SimpleTestCase):
    def test_components_charges_and_no_double_counting(self):
        from .report import build_report
        def row(n,kind,name,base=None,index=None,cur=None,percent=None,parent=2):
            v=[None]*12;v[2]=name;v[9]=base;v[10]=index;v[11]=cur;v[6]=percent
            if kind=='section':v[0]=name
            return dict(source_row=n,kind=kind,parent_row=parent,values=v)
        rows=[row(1,'section','Работа'),row(2,'item','Подработа'),row(3,'detail','ОТ',100,2),row(4,'detail','ЭМ',50,3),row(5,'detail','в т.ч. ОТм',10,2),row(6,'detail','М',20,4),row(7,'detail','НР Работы',percent=100),row(8,'detail','СП Работы',percent=50),row(9,'detail','Всего по позиции',999999,None,999999),row(10,'total','ВСЕГО по смете',999999,None,999999)]
        report=build_report(rows)
        self.assertEqual(report['base'],335)
        self.assertEqual(report['current'],760)
        rows[-1]['values'][11]=0;rows[-2]['values'][11]=0
        self.assertEqual(build_report(rows)['current'],760)
        self.assertTrue(report['diagnostics'])
    def test_incomplete_is_unknown_not_zero(self):
        from .report import build_report
        d=parse(sample(),'sample.xlsx');report=build_report(d['rows'])
        self.assertIsNone(report['current']);self.assertTrue(report['diagnostics'])


class DeleteTests(AccessTests):
    def test_delete_confirmation_scope_and_catalog_preservation(self):
        self.client.post(reverse('estimates:estimate_import'),{'object_name':'Test','file':SimpleUploadedFile('a.xlsx',sample())})
        doc=EstimateDocument.objects.get();url=reverse('estimates:estimate_delete',args=[doc.pk]);count=EstimateCatalog.objects.count()
        self.assertContains(self.client.get(url),'Удалить смету?')
        self.assertTrue(EstimateDocument.objects.filter(pk=doc.pk).exists())
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save()
        self.assertEqual(self.client.post(url).status_code,403)
        self.user.role=self.role;self.user.save()
        other=Company.objects.create(name='Hidden');self.user.company=other;self.user.save()
        self.assertEqual(self.client.post(url).status_code,404)
        self.user.company=self.company;self.user.save()
        self.assertEqual(self.client.post(url).status_code,302)
        self.assertFalse(EstimateDocument.objects.filter(pk=doc.pk).exists())
        self.assertEqual(EstimateCatalog.objects.count(),count)


class NumberTests(SimpleTestCase):
    def test_precision_grouping_and_zero(self):
        from .templatetags.estimate_tags import estimate_quantity
        from apps.analytics.templatetags.revenue_tags import money
        self.assertEqual(estimate_quantity('1234.000008'),'1\u00a0234,000008')
        self.assertEqual(estimate_quantity(0),'0')
        self.assertEqual(estimate_quantity('0.000008'),'0,000008')
        self.assertEqual(estimate_quantity(None),'—')
        self.assertEqual(money(4047806),'4 047 806')


class ExportReportTests(AccessTests):
    def test_two_exports_and_summary_filter(self):
        from .models import EstimateObject
        for obj in ['First','Second']:
            self.client.post(reverse('estimates:estimate_import'),{'object_name':obj,'file':SimpleUploadedFile('a.xlsx',sample())})
        doc=EstimateDocument.objects.filter(estimate_object__name='First').get()
        for mode in ['full','works']:
            for params in [{'mode':mode},{'mode':mode,'id':doc.pk}]:
                response=self.client.get(reverse('estimates:estimate_export'),params)
                self.assertEqual(response.status_code,200)
                w=load_workbook(BytesIO(response.content))
                self.assertEqual(w['Свод смет'].max_row,3 if 'id' in params else 4)
                if mode=='works':
                    self.assertEqual(set(w.sheetnames),{'Свод смет','Работы и подработы'})
                    self.assertNotIn('Ресурсов',[c.value for c in w['Свод смет'][1]])
                else:self.assertIn('Ресурсы',w.sheetnames)
        response=self.client.get(reverse('estimates:estimate_report_list'),{'object':doc.estimate_object_id})
        self.assertContains(response,'First');self.assertEqual(len(response.context['groups']),1)
        self.assertContains(response,'mode=works')
        self.user.role=Role.objects.get(code='OBSERVER');self.user.save()
        self.assertEqual(self.client.get(reverse('estimates:estimate_report_list')).status_code,200)
        other=EstimateObject.objects.create(company=Company.objects.create(name='Secret'),name='Hidden')
        self.assertEqual(self.client.get(reverse('estimates:estimate_report_list'),{'object':other.pk}).status_code,404)
        self.assertEqual(self.client.get(reverse('estimates:estimate_export'),{'object':other.pk}).status_code,404)
        self.assertEqual(self.client.get(reverse('estimates:estimate_export'),{'mode':'unknown'}).status_code,400)
