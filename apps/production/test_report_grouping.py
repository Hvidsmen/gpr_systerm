from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from openpyxl import load_workbook
from django.test import TestCase
from django.urls import reverse
from apps.projects.models import Section
from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
from apps.resources.models import BrigadeGroup,EquipmentCategory,EquipmentType
from apps.planning.models import DailyScheduleEntry
from .models import DailyFact,EquipmentActivity
from .smg_period_report import build
from .report_export import workbook
from .production_entry import report_data


class ReportGroupingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_smg_period_report import SmgPeriodReportTests
        SmgPeriodReportTests.setUpTestData.__func__(cls)
        cls.objects=[];cls.items=[]
        for index in (1,2):
            obj=ComplexObject.objects.create(company=cls.company,section=cls.work.section,construction_object=cls.obj,name=f'ПК-{index}')
            work=ProjectWork.objects.create(company=cls.company,section=cls.work.section,complex_object=obj,name=f'Комплекс {index}',kind='COMPOSITE',unit='компл')
            item=ProjectWorkItem.objects.create(company=cls.company,project_work=work,name='Установка камеры',unit='шт')
            cls.objects.append(obj);cls.items.append(item)
            DailyScheduleEntry.objects.create(schedule=cls.schedule,key=f'camera:{index}',kind='work',label=work.name,
                metadata={'id':work.pk,'kind':'COMPOSITE'},rows=[{'date':str(cls.start),'item_id':item.pk,'quantity':str(index+1)},
                {'date':str(cls.later),'item_id':item.pk,'quantity':str(index)}])
            DailyFact.objects.create(company=cls.company,project_work=work,work_item=item,date=cls.start,actual_quantity=index)
        cls.category=EquipmentCategory.objects.create(company=cls.company,name='Специализированная техника',sort_order=2)
        EquipmentType.objects.filter(pk=cls.type.pk).update(category=cls.category)
        cls.staff_group=BrigadeGroup.objects.create(company=cls.company,name='Основной персонал',sort_order=3)
        type(cls.brigade).objects.filter(pk=cls.brigade.pk).update(group=cls.staff_group)
        for kind,values in [('labor',{'brigade_id':cls.brigade.pk,'planned_count':'2'}),('equipment',{'equipment_type_id':cls.type.pk,'planned_count':'2'})]:
            DailyScheduleEntry.objects.create(schedule=cls.schedule,key=f'groups:{kind}',kind=kind,label=kind,rows=[{'date':str(cls.start),**values}])
        EquipmentActivity.objects.create(company=cls.company,construction_object=cls.obj,equipment=cls.machine,date=cls.start,shift=1,state='WORKING')

    def camera(self,report):
        return next(work for section in report['work_groups'] for work in section['works'] if work['label']=='Установка камеры')

    def test_section_work_object_hierarchy_and_totals_in_work_row(self):
        report=build(self.obj,self.selected)
        work=self.camera(report)
        self.assertEqual([obj['label'] for obj in work['objects']],['ПК-1','ПК-2'])
        self.assertEqual((work['total']['plan'],work['total']['actual']),(8,3))
        self.assertEqual([obj['total']['actual'] for obj in work['objects']],[1,2])
        self.assertEqual(sum(obj['total']['plan'] for obj in work['objects']),work['total']['plan'])

    def test_same_caption_different_units_are_not_added(self):
        item=self.items[1];item.unit='м';item.save()
        report=build(self.obj,self.selected)
        groups=[work for section in report['work_groups'] for work in section['works'] if work['label']=='Установка камеры']
        self.assertEqual(len(groups),2)
        self.assertEqual({work['unit'] for work in groups},{'м','шт'})

    def test_distinct_sections_never_merge_even_with_same_caption(self):
        section=Section.objects.create(company=self.company,construction_object=self.obj,name='Другой раздел')
        ProjectWork.objects.filter(pk=self.items[1].project_work_id).update(section=section)
        report=build(self.obj,self.selected)
        matching=[group for group in report['work_groups'] if any(w['label']=='Установка камеры' for w in group['works'])]
        self.assertEqual(len(matching),2)

    def test_daily_plan_fact_and_missing_days_preserved(self):
        work=self.camera(build(self.obj,self.selected))
        days={row['date']:row for row in work['total']['daily_rows']}
        self.assertEqual((days[self.start]['plan'],days[self.start]['actual'],days[self.start]['deviation']),(5,3,-2))
        self.assertEqual(days[self.later]['plan'],3)
        self.assertIsNone(days[self.later]['actual'])
        self.assertIsNone(days[self.start+timedelta(days=1)]['plan'])
        self.assertIsNone(days[self.start+timedelta(days=1)]['actual'])
        self.assertEqual(sum(row['plan'] or 0 for row in days.values()),work['total']['plan'])

    def test_resource_catalogue_groups_and_root_category(self):
        report=build(self.obj,self.selected)
        self.assertEqual(report['labor_groups'][0]['label'],self.staff_group.name)
        self.assertEqual(report['equipment_groups'][0]['label'],self.category.name)
        self.assertEqual(report['equipment_groups'][0]['total']['actual'],1)

    def test_custom_group_order_and_ungrouped_last(self):
        first=EquipmentCategory.objects.create(company=self.company,name='Я — первая',sort_order=1)
        other=EquipmentType.objects.create(company=self.company,name='Самосвал',category=first)
        ungrouped=EquipmentType.objects.create(company=self.company,name='Без группы')
        for equipment in [other,ungrouped]:
            DailyScheduleEntry.objects.create(schedule=self.schedule,key=f'extra:{equipment.pk}',kind='equipment',rows=[{'date':str(self.start),'equipment_type_id':equipment.pk,'planned_count':'0'}])
        self.assertEqual([g['label'] for g in build(self.obj,self.selected)['equipment_groups']],[first.name,self.category.name,'Без группы'])

    def test_day_report_uses_only_selected_day(self):
        day=build(self.obj,self.start,day_only=True)
        self.assertEqual(day['start'],day['end'])
        self.assertEqual(self.camera(day)['total']['plan'],5)
        self.assertEqual(day['equipment'][0]['unit'],'ед.')
        self.client.force_login(self.users['DISPATCHER'])
        response=self.client.get(reverse('production:smg_period_detail'),{'construction_object':self.obj.pk,'date':self.start,'scope':'day'})
        self.assertContains(response,self.category.name)
        self.assertContains(response,'План за день')

    def test_month_html_lazy_daily_mode_and_excel(self):
        self.client.force_login(self.users['DISPATCHER'])
        query={'construction_object':self.obj.pk,'date':self.selected}
        normal=self.client.get(reverse('production:smg_period_detail'),query)
        self.assertContains(normal,'Раскрыть по дням')
        self.assertNotContains(normal,'production-report-days')
        detailed=self.client.get(reverse('production:smg_period_detail'),{**query,'by_day':'1'})
        self.assertContains(detailed,'production-report-days')
        self.assertContains(detailed,self.later.strftime('%d.%m.%Y'))
        response=self.client.get(reverse('production:smg_period_export'),{**query,'by_day':'1'})
        book=load_workbook(BytesIO(b''.join(response.streaming_content)))
        self.assertIn('Работы · дни',book.sheetnames)
        self.assertIn('Люди · дни',book.sheetnames)
        self.assertIn('Техника · дни',book.sheetnames)
        values=list(book['Работы'].values)
        total=next(row for row in values if row[0]=='Установка камеры')
        self.assertEqual((total[3],total[4]),(8,3))
        self.assertTrue(any(row[2]=='ПК-2' for row in book['Работы · дни'].values))

    def test_day_excel_work_totals_and_grouped_equipment(self):
        book=load_workbook(workbook(self.obj,self.start,report_data(self.obj,self.start)))
        rows=list(book.active.values)
        subtotal=next(row for row in rows if row[0]=='Установка камеры')
        self.assertEqual(subtotal[4],3)
        self.assertIn('Техника',book.sheetnames)
        self.assertTrue(any(row[0]==self.category.name for row in book['Техника'].values))
