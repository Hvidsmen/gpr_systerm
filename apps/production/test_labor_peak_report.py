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
        self.fact(self.brigade,self.selected,3)
        self.fact(self.brigade,self.later,5)
        return build(self.obj,self.selected)

    def test_selected_date_replaces_daily_maximum_and_recalculates_metrics(self):
        report=self.example()
        for row in [report['labor'][0],report['labor_groups'][0]['total']]:
            self.assertEqual((row['plan'],row['actual'],row['deviation'],row['percent']),(4,3,-1,75))
            self.assertEqual(row['unit'],'чел.')
        row=report['labor'][0]
        self.assertEqual(row['recorded_days'],3)
        days={day['date']:day for day in row['daily_rows']}
        self.assertEqual((days[self.start]['plan'],days[self.start]['actual']),(4,2))
        self.assertEqual((days[self.later]['actual'],days[self.later]['deviation']),(5,1))
        self.assertEqual(days[self.selected]['actual'],3)

    def test_group_total_uses_selected_date_sum(self):
        self.plan(self.brigade,[(self.start,8),(self.later,1)])
        self.plan(self.second,[(self.start,1),(self.later,8)])
        for brigade,values in [(self.brigade,[(self.start,3),(self.later,1)]),
                               (self.second,[(self.start,2),(self.later,5)])]:
            for day,count in values:self.fact(brigade,day,count)
        report=build(self.obj,self.later)
        total=report['labor_groups'][0]['total']
        self.assertEqual((total['plan'],total['actual'],total['deviation']),(9,6,-3))
        self.assertEqual(total['percent'],Decimal(6)/9*100)
        self.assertEqual(sum(row['plan'] for row in report['labor']),9)
        self.assertEqual(sum(row['actual'] for row in report['labor']),6)
        block=next(row for row in day_matrix(report)['blocks'] if row.get('toggle')=='labor-0')
        self.assertEqual([metric['total'] for metric in block['metrics'][:3]],[9,6,-3])
        self.assertEqual(block['metrics'][1]['cells'][0]['value'],5)

    def test_missing_values_and_saved_zero_are_distinct(self):
        self.plan(self.brigade,[(self.start,0)])
        row=build(self.obj,self.start)['labor'][0]
        self.assertIsNone(row['actual'])
        self.assertIsNone(row['deviation'])
        self.fact(self.brigade,self.start,0)
        row=build(self.obj,self.start)['labor'][0]
        self.assertEqual((row['plan'],row['actual'],row['deviation']),(0,0,0))
        self.assertIsNone(row['percent'])
        self.fact(self.second,self.start,3)
        row=next(row for row in build(self.obj,self.start)['labor'] if row['label']==self.second.name)
        self.assertIsNone(row['plan'])
        self.assertEqual(row['actual'],3)
        self.assertIsNone(row['deviation'])

    def test_partial_selected_date_does_not_claim_complete_comparison(self):
        for brigade in [self.brigade,self.second]:
            self.plan(brigade,[(self.start,4),(self.later,4)])
        self.fact(self.brigade,self.start,5)
        self.fact(self.second,self.later,6)
        report=build(self.obj,self.start)
        total=report['labor_groups'][0]['total']
        self.assertEqual((total['plan'],total['actual']),(8,5))
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
        report=build(self.obj,self.later)
        self.assertEqual(report['works'][0]['plan'],12)
        self.assertEqual((report['equipment'][0]['plan'],report['equipment'][0]['actual']),(3,3))
        self.assertEqual(report['equipment'][0]['unit'],'ед.')

    def test_html_matrix_compact_view_and_excel_share_selected_date_totals(self):
        self.example()
        self.client.force_login(self.users['DISPATCHER'])
        query={'construction_object':self.obj.pk,'date':self.selected}
        response=self.client.get(reverse('production:smg_period_detail'),query)
        self.assertContains(response,f'План на {self.selected:%d.%m.%Y}')
        self.assertNotContains(response,'чел.·дн.')
        response=self.client.get(reverse('production:smg_period_detail'),{**query,'by_day':'1'})
        self.assertContains(response,f'<th scope="col" class="report-day-period">На {self.selected:%d.%m}</th>',html=True)
        export=self.client.get(reverse('production:smg_period_export'),{**query,'by_day':'1'})
        book=load_workbook(BytesIO(b''.join(export.streaming_content)))
        rows=list(book['Люди'].values)
        self.assertEqual(rows[3][3:5],(f'План на {self.selected:%d.%m.%Y}',f'Факт на {self.selected:%d.%m.%Y}'))
        for name in [self.brigade.name,'Итого · '+self.group.name]:
            row=next(row for row in rows if row[0]==name)
            self.assertEqual(row[2:7],('чел.',4,3,-1,75))
        daily=list(book['Люди · дни'].values)
        row=next(row for row in daily[4:] if row[1]==self.brigade.name and row[3]=='Факт')
        self.assertEqual(row[4],2)
        self.assertIsNone(row[5])
        self.assertEqual(row[4+(self.later-self.start).days],5)
