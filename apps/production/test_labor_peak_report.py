from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from apps.planning.models import DailyScheduleEntry
from apps.resources.models import Brigade, BrigadeGroup
from .models import LaborFact, EquipmentFact
from .report_grouping import day_matrix
from .smg_period_report import build


class LaborPeakReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_smg_period_report import SmgPeriodReportTests
        SmgPeriodReportTests.setUpTestData.__func__(cls)
        cls.group=BrigadeGroup.objects.create(company=cls.company,name='Персонал для проверки')
        Brigade.objects.filter(pk=cls.brigade.pk).update(group=cls.group)
        cls.second=Brigade.objects.create(company=cls.company,name='Другая должность',group=cls.group)

    def plan(self, brigade, values):
        DailyScheduleEntry.objects.create(schedule=self.schedule,kind='labor',key=f'peak:{brigade.pk}',
            rows=[{'date':str(day),'brigade_id':brigade.pk,'planned_count':str(count)} for day,count in values])

    def fact(self, brigade, day, count):
        LaborFact.objects.create(company=self.company,construction_object=self.obj,
            brigade=brigade,date=day,actual_workers=count)

    def example(self):
        end=build(self.obj,self.selected)['end']
        self.plan(self.brigade,[(self.start+timedelta(days=n),4) for n in range((end-self.start).days+1)])
        self.fact(self.brigade,self.start,2)
        self.fact(self.brigade,self.later,5)
        return build(self.obj,self.selected)

    def test_daily_maximum_replaces_person_days_and_recalculates_metrics(self):
        report=self.example()
        for row in [report['labor'][0],report['labor_groups'][0]['total']]:
            self.assertEqual((row['plan'],row['actual'],row['deviation'],row['percent']),(4,5,1,125))
            self.assertEqual(row['unit'],'чел.')
        row=report['labor'][0]
        self.assertEqual(row['recorded_days'],2)
        days={day['date']:day for day in row['daily_rows']}
        self.assertEqual((days[self.start]['plan'],days[self.start]['actual']),(4,2))
        self.assertEqual((days[self.later]['actual'],days[self.later]['deviation']),(5,1))
        self.assertIsNone(days[self.start+timedelta(days=1)]['actual'])

    def test_group_uses_maximum_of_daily_sums_with_peaks_on_different_dates(self):
        self.plan(self.brigade,[(self.start,8),(self.later,1)])
        self.plan(self.second,[(self.start,1),(self.later,8)])
        for brigade,values in [(self.brigade,[(self.start,3),(self.later,1)]),
                               (self.second,[(self.start,2),(self.later,5)])]:
            for day,count in values:self.fact(brigade,day,count)
        report=build(self.obj,self.selected)
        total=report['labor_groups'][0]['total']
        self.assertEqual((total['plan'],total['actual'],total['deviation']),(9,6,-3))
        self.assertEqual(total['percent'],Decimal(6)/9*100)
        self.assertEqual(sum(row['plan'] for row in report['labor']),16)
        self.assertEqual(sum(row['actual'] for row in report['labor']),8)
        block=next(row for row in day_matrix(report)['blocks'] if row.get('toggle')=='labor-0')
        self.assertEqual([metric['total'] for metric in block['metrics'][:3]],[9,6,-3])
        self.assertEqual(block['metrics'][1]['cells'][0]['value'],5)

    def test_missing_values_and_saved_zero_are_distinct(self):
        self.plan(self.brigade,[(self.start,0)])
        row=build(self.obj,self.selected)['labor'][0]
        self.assertIsNone(row['actual'])
        self.assertIsNone(row['deviation'])
        self.fact(self.brigade,self.start,0)
        row=build(self.obj,self.selected)['labor'][0]
        self.assertEqual((row['plan'],row['actual'],row['deviation']),(0,0,0))
        self.assertIsNone(row['percent'])
        self.fact(self.second,self.start,3)
        row=next(row for row in build(self.obj,self.selected)['labor'] if row['label']==self.second.name)
        self.assertIsNone(row['plan'])
        self.assertEqual(row['actual'],3)
        self.assertIsNone(row['deviation'])

    def test_partial_group_peak_does_not_claim_complete_comparison(self):
        for brigade in [self.brigade,self.second]:
            self.plan(brigade,[(self.start,4),(self.later,4)])
        self.fact(self.brigade,self.start,5)
        self.fact(self.second,self.later,6)
        report=build(self.obj,self.selected)
        total=report['labor_groups'][0]['total']
        self.assertEqual((total['plan'],total['actual']),(8,6))
        self.assertEqual((total['actual_count'],total['row_count']),(1,2))
        self.assertIsNone(total['deviation'])
        self.assertIsNone(total['percent'])
        day=total['daily_rows'][0]
        self.assertEqual((day['actual_count'],day['row_count']),(1,2))
        self.assertIsNone(day['deviation'])
        self.assertIsNone(day['percent'])
        block=next(row for row in day_matrix(report)['blocks'] if row.get('toggle')=='labor-0')
        self.assertTrue(block['metrics'][1]['total_partial'])

    def test_selected_day_works_and_equipment_counts(self):
        self.example()
        row=build(self.obj,self.start,day_only=True)['labor'][0]
        self.assertEqual((row['plan'],row['actual'],row['deviation'],row['percent']),(4,2,-2,50))
        DailyScheduleEntry.objects.create(schedule=self.schedule,kind='equipment',key='peak:equipment',rows=[
            {'date':str(self.start),'equipment_type_id':self.type.pk,'planned_count':'2'},
            {'date':str(self.later),'equipment_type_id':self.type.pk,'planned_count':'3'}])
        for day,count in [(self.start,2),(self.later,3)]:
            EquipmentFact.objects.create(company=self.company,construction_object=self.obj,
                equipment_type=self.type,date=day,actual_count=count)
        report=build(self.obj,self.selected)
        self.assertEqual(report['works'][0]['plan'],12)
        self.assertEqual((report['equipment'][0]['plan'],report['equipment'][0]['actual']),(3,3))
        self.assertEqual(report['equipment'][0]['unit'],'ед.')

    def test_html_matrix_compact_view_and_excel_share_peak_totals(self):
        self.example()
        self.client.force_login(self.users['DISPATCHER'])
        query={'construction_object':self.obj.pk,'date':self.selected}
        response=self.client.get(reverse('production:smg_period_detail'),query)
        self.assertContains(response,'План · максимум за день')
        self.assertNotContains(response,'чел.·дн.')
        response=self.client.get(reverse('production:smg_period_detail'),{**query,'by_day':'1'})
        self.assertContains(response,'<th scope="col" class="report-day-period">Максимум за день</th>',html=True)
        export=self.client.get(reverse('production:smg_period_export'),{**query,'by_day':'1'})
        book=load_workbook(BytesIO(b''.join(export.streaming_content)))
        rows=list(book['Люди'].values)
        self.assertEqual(rows[3][3:5],('План · максимум за день','Факт · максимум за день'))
        for name in [self.brigade.name,'Итого · '+self.group.name]:
            row=next(row for row in rows if row[0]==name)
            self.assertEqual(row[2:7],('чел.',4,5,1,125))
        daily=list(book['Люди · дни'].values)
        row=next(row for row in daily[4:] if row[1]==self.brigade.name and row[3]=='Факт')
        self.assertEqual(row[4],2)
        self.assertIsNone(row[5])
        self.assertEqual(row[4+(self.later-self.start).days],5)
