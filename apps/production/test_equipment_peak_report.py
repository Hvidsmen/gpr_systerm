from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.planning.models import DailyScheduleEntry
from apps.resources.models import EquipmentCategory, EquipmentType
from .models import EquipmentFact
from .report_grouping import day_matrix
from .smg_period_report import build


class EquipmentPeakReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_smg_period_report import SmgPeriodReportTests
        SmgPeriodReportTests.setUpTestData.__func__(cls)
        cls.category=EquipmentCategory.objects.create(company=cls.company,name='Техника для проверки')
        EquipmentType.objects.filter(pk=cls.type.pk).update(category=cls.category)
        cls.second=EquipmentType.objects.create(company=cls.company,name='Другая техника',category=cls.category)

    def plan(self,equipment_type,values):
        DailyScheduleEntry.objects.create(schedule=self.schedule,kind='equipment',key=f'peak:{equipment_type.pk}',
            rows=[{'date':str(day),'equipment_type_id':equipment_type.pk,'planned_count':str(count)} for day,count in values])

    def fact(self,equipment_type,day,count):
        EquipmentFact.objects.create(company=self.company,construction_object=self.obj,
            equipment_type=equipment_type,date=day,actual_count=count)

    def example(self):
        end=build(self.obj,self.selected)['end']
        self.plan(self.type,[(self.start+timedelta(days=n),1) for n in range((end-self.start).days+1)])
        self.fact(self.type,self.start,0)
        self.fact(self.type,self.later,2)
        return build(self.obj,self.selected)

    def test_month_uses_peak_count_and_selected_day_keeps_daily_values(self):
        report=self.example()
        for row in [report['equipment'][0],report['equipment_groups'][0]['total']]:
            self.assertEqual((row['plan'],row['actual'],row['deviation'],row['percent']),(1,2,1,200))
            self.assertEqual(row['unit'],'ед.')
        row=report['equipment'][0]
        self.assertEqual(row['recorded_days'],2)
        days={day['date']:day for day in row['daily_rows']}
        self.assertEqual((days[self.start]['plan'],days[self.start]['actual']),(1,0))
        self.assertEqual(days[self.later]['actual'],2)
        self.assertIsNone(days[self.start+timedelta(days=1)]['actual'])
        day=build(self.obj,self.start,day_only=True)['equipment'][0]
        self.assertEqual((day['plan'],day['actual'],day['deviation'],day['percent']),(1,0,-1,0))
        self.assertEqual(report['works'][0]['plan'],12)

    def test_group_peak_is_maximum_daily_sum_not_sum_of_type_peaks(self):
        self.plan(self.type,[(self.start,8),(self.later,1)])
        self.plan(self.second,[(self.start,1),(self.later,8)])
        for equipment_type,values in [(self.type,[(self.start,3),(self.later,1)]),
                                       (self.second,[(self.start,2),(self.later,5)])]:
            for day,count in values:self.fact(equipment_type,day,count)
        report=build(self.obj,self.selected)
        total=report['equipment_groups'][0]['total']
        self.assertEqual((total['plan'],total['actual'],total['deviation']),(9,6,-3))
        self.assertEqual(total['percent'],Decimal(6)/9*100)
        self.assertEqual(sum(row['plan'] for row in report['equipment']),16)
        self.assertEqual(sum(row['actual'] for row in report['equipment']),8)
        block=next(row for row in day_matrix(report)['blocks'] if row.get('toggle')=='equipment-0')
        self.assertEqual([metric['total'] for metric in block['metrics'][:3]],[9,6,-3])
        self.assertEqual(block['metrics'][1]['cells'][0]['value'],5)

    def test_missing_actual_saved_zero_and_unplanned_type_remain_distinct(self):
        self.plan(self.type,[(self.start,0)])
        row=build(self.obj,self.selected)['equipment'][0]
        self.assertIsNone(row['actual'])
        self.assertIsNone(row['deviation'])
        self.fact(self.type,self.start,0)
        row=build(self.obj,self.selected)['equipment'][0]
        self.assertEqual((row['plan'],row['actual'],row['deviation']),(0,0,0))
        self.assertIsNone(row['percent'])
        self.fact(self.second,self.start,3)
        row=next(row for row in build(self.obj,self.selected)['equipment'] if row['label']==self.second.name)
        self.assertIsNone(row['plan'])
        self.assertEqual(row['actual'],3)
        self.assertIsNone(row['percent'])

    def test_incomplete_group_peak_keeps_coverage_and_missing_comparisons(self):
        for equipment_type in [self.type,self.second]:
            self.plan(equipment_type,[(self.start,4),(self.later,4)])
        self.fact(self.type,self.start,5)
        self.fact(self.second,self.later,6)
        report=build(self.obj,self.selected)
        total=report['equipment_groups'][0]['total']
        self.assertEqual((total['plan'],total['actual']),(8,6))
        self.assertEqual((total['actual_count'],total['row_count']),(1,2))
        self.assertIsNone(total['deviation'])
        self.assertIsNone(total['percent'])
        day=total['daily_rows'][0]
        self.assertEqual((day['actual_count'],day['row_count']),(1,2))
        self.assertIsNone(day['percent'])
        block=next(row for row in day_matrix(report)['blocks'] if row.get('toggle')=='equipment-0')
        self.assertTrue(block['metrics'][1]['total_partial'])

    def test_html_and_excel_use_same_peaks_in_full_and_equipment_only_reports(self):
        self.example()
        self.client.force_login(self.users['DISPATCHER'])
        for equipment_only in ['0','1']:
            with self.subTest(equipment_only=equipment_only):
                query={'construction_object':self.obj.pk,'date':self.selected,'equipment_only':equipment_only}
                response=self.client.get(reverse('production:smg_period_detail'),query)
                self.assertContains(response,'План · максимум за день')
                self.assertNotContains(response,'маш.·дн.')
                response=self.client.get(reverse('production:smg_period_detail'),{**query,'by_day':'1'})
                self.assertContains(response,'<th scope="col" class="report-day-period">Максимум за день</th>',html=True)
                export=self.client.get(reverse('production:smg_period_export'),{**query,'by_day':'1'})
                book=load_workbook(BytesIO(b''.join(export.streaming_content)))
                rows=list(book['Техника'].values)
                self.assertEqual(rows[3][3:5],('План · максимум за день','Факт · максимум за день'))
                for label in [self.type.name,'Итого · '+self.category.name]:
                    row=next(row for row in rows if row[0]==label)
                    self.assertEqual(row[2:7],('ед.',1,2,1,200))
                daily=list(book['Техника · дни'].values)
                row=next(row for row in daily[4:] if row[1]==self.type.name and row[3]=='Факт')
                self.assertEqual(row[4],0)
                self.assertIsNone(row[5])
                self.assertEqual(row[4+(self.later-self.start).days],2)
