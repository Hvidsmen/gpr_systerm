from io import BytesIO
from openpyxl import load_workbook
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from apps.accounts.models import Role,User,Company
from apps.projects.models import ConstructionObject,Project
from .smg_period_report import build


class MechanizationReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_report_grouping import ReportGroupingTests
        ReportGroupingTests.setUpTestData.__func__(cls)
        from .models import EmployeeActivity
        EmployeeActivity.objects.create(company=cls.company,construction_object=cls.obj,date=cls.start,shift=1,employee=cls.employee,position=cls.employee.position,work=cls.items[0].project_work,work_item=cls.items[0])

    def setUp(self):
        self.user=self.users['MECHANIZATION_DISPATCHER']
        self.client.force_login(self.user)
        self.query={'construction_object':self.obj.pk,'date':str(self.start)}

    def test_own_report_has_day_and_period_without_changing_production_direction(self):
        self.assertContains(self.client.get(reverse('production:equipment_report')),'Показать отчёт')
        default=self.client.get(reverse('production:equipment_report'),self.query)
        self.assertFalse(default.context['day_only'])
        day=self.client.get(reverse('production:equipment_report'),{**self.query,'scope':'day'})
        self.assertEqual(day.status_code,200)
        self.assertFalse(day.context['equipment_only']);self.assertTrue(day.context['day_only'])
        self.assertContains(day,'Установка камеры')
        self.assertContains(day,self.employee.display_name)
        self.assertContains(day,'План за день')
        month=self.client.get(reverse('production:equipment_report'),{**self.query,'scope':'period'})
        self.assertEqual(month.status_code,200)
        self.assertContains(month,'Раскрыть по дням')
        self.assertContains(month,self.category.name)
        from .dispatch import sections_for
        self.assertEqual(sections_for(self.user),['mechanization'])

    def test_shared_report_urls_show_works_people_equipment_but_not_fuel(self):
        response=self.client.get(reverse('production:smg_period_detail'),{**self.query,'equipment_only':'0','by_day':'1'})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Установка камеры')
        self.assertContains(response,'Работы ·')
        self.assertContains(response,'Люди ·')
        self.assertNotContains(response,'smg-period-fuel')
        self.assertContains(response,'production-report-days')
        self.assertTrue(response.context['works'])
        self.assertTrue(response.context['labor'])
        self.assertEqual(response.context['fuel'],[])

    def test_report_reads_all_summary_facts_without_warehouse_fuel(self):
        with CaptureQueriesContext(connection) as queries:
            report=build(self.obj,self.start,include_fuel=False)
        sql=' '.join(query['sql'].lower() for query in queries)
        for table in ['production_dailyfact','production_employeeactivity','production_laborfact']:
            self.assertIn(table,sql,table)
        self.assertNotIn('production_campfuelday',sql)
        self.assertEqual(report['equipment_groups'][0]['label'],self.category.name)

    def test_excel_contains_works_people_and_equipment_and_role_keeps_input_limits(self):
        response=self.client.get(reverse('production:smg_period_export'),{**self.query,'by_day':'1','equipment_only':'0'})
        self.assertEqual(response.status_code,200)
        book=load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertEqual(book.sheetnames,['Работы','Люди','Техника','Работы · дни','Люди · дни','Техника · дни'])
        text=' '.join(str(cell.value or '') for page in book for row in page for cell in row)
        self.assertIn('Установка камеры',text)
        self.assertNotIn('Дизельное топливо',text)
        self.assertIn(self.type.name,text)
        self.assertEqual(self.client.get(reverse('production:production_report_download'),self.query).status_code,200)
        self.assertEqual(self.client.post(self.url,{**self.query,'section':'works'}).status_code,403)
        self.assertEqual(self.client.post(self.url,{**self.query,'section':'labor'}).status_code,403)
        self.assertEqual(self.client.post(reverse('production:equipment_report'),self.query).status_code,405)

    def test_company_and_assigned_objects_are_enforced(self):
        self.assertEqual(self.client.get(reverse('production:smg_period_detail'),{**self.query,'construction_object':self.other.pk}).status_code,400)
        company=Company.objects.create(name='Чужая компания')
        project=Project.objects.create(company=company,name='Чужой проект')
        obj=ConstructionObject.objects.create(company=company,project=project,name='Чужой объект')
        response=self.client.get(reverse('production:equipment_report'),{**self.query,'construction_object':obj.pk})
        self.assertFalse(response.context['selector'].is_valid());self.assertNotIn('equipment',response.context)

    def test_production_dispatcher_keeps_full_report_and_has_period_entry_link(self):
        self.client.force_login(self.users['DISPATCHER'])
        response=self.client.get(reverse('production:smg_period_detail'),self.query)
        self.assertContains(response,'Установка камеры')
        self.assertContains(response,'smg-period-fuel')
        page=self.client.get(reverse('production:equipment_report'),self.query)
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'План-факт СМГ')
        self.assertContains(page,'smg-period-fuel')
        self.assertFalse(page.context['day_only'])

    def test_menu_has_read_only_summary_without_production_entry(self):
        response=self.client.get(reverse('production:equipment_report'),self.query)
        self.assertContains(response,'План-факт СМГ')
        self.assertNotContains(response,'> Производство</a>')
