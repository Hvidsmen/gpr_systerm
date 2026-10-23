from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from django.test import SimpleTestCase
from .pivot_engine import validate_config
from .pivot_layout import build_layout

class PivotLayoutTests(SimpleTestCase):
    report={'columns':[{'date':date(2026,1,1)},{'date':date(2026,2,1)}]}
    def record(self,name,p,f,unit='шт',kind='works',operation='sum',total=None):
        cells=[{'plan':D(x),'fact':D(y)} for x,y in zip(p,f)]
        return {'building':'КТП','complex_object':'УЗА','package':'Комплекс','work':name,'kind':kind,
                'row':{'unit':unit,'operation':operation,'cells':cells,'total':total or {'plan':sum(map(D,p)),'fact':sum(map(D,f))}}}
    def config(self,**updates):
        value={'rows':['building','work'],'columns':['month'],'measures':['plan','fact','completion'],'formulas':[], 'view':'hierarchy',
               'totals':{'row_levels':['building'],'column_levels':[],'row_grand':True,'column_grand':True,'position':'after'}}
        value.update(updates);return validate_config(value)
    def test_subtotals_and_percentages_are_computed_from_sources(self):
        rows=[self.record('А',[10,20],[5,10]),self.record('Б',[90,80],[90,80])]
        grid=build_layout(rows,self.report,self.config())
        grand=next(row for row in grid['rows'] if row['kind']=='grand' and row['measure']=='completion')
        self.assertEqual(grand['values'],[D(95),D(90),D('92.5')])
        subtotal=next(row for row in grid['rows'] if row['kind']=='subtotal' and row['measure']=='plan')
        self.assertEqual(subtotal['values'],[D(100),D(100),D(200)])
        self.assertTrue(any(row['kind']=='group' for row in grid['rows']))
    def test_period_total_uses_average_and_latest_stock_instead_of_summing_months(self):
        rows=[self.record('Люди',[2,4],[2,4],unit='чел.',kind='labor',operation='average',total={'plan':D(3),'fact':D(3)}),self.record('Топливо',[100,60],[90,50],unit='л',kind='fuel',operation='last',total={'plan':D(60),'fact':D(50)})]
        grid=build_layout(rows,self.report,self.config(measures=['plan']))
        totals={r['unit']:r['values'][-1] for r in grid['rows'] if r['kind']=='grand'}
        self.assertEqual(totals,{'чел.':D(3),'л':D(60)})
    def test_table_mode_totals_can_be_disabled(self):
        config=self.config(view='table',totals={})
        grid=build_layout([self.record('А',[1,2],[1,2])],self.report,config)
        self.assertTrue(all(r['kind']=='detail' for r in grid['rows']))
        self.assertEqual(len(grid['columns']),2)
    def test_nested_column_subtotals_and_header_spans(self):
        rows=[self.record('А',[1,2],[1,2]),self.record('Б',[3,4],[3,4])]
        config=self.config(rows=['building'],columns=['package','work','month'],measures=['plan'],totals={'row_levels':[],'column_levels':['package','work'],'row_grand':False,'column_grand':True,'position':'after'})
        grid=build_layout(rows,self.report,config)
        row=next(r for r in grid['rows'] if r['kind']=='detail')
        amounts={col['label']:value for col,value in zip(grid['columns'],row['values'])}
        self.assertEqual(amounts['Итого: Комплекс'],D(10));self.assertEqual(amounts['Общий итог'],D(10))
        self.assertEqual(grid['header_depth'],3)
        self.assertEqual(sum(c['colspan'] for c in grid['headers'][0]),len(grid['columns']))
    def test_subtotal_position_is_configurable_and_units_do_not_mix(self):
        rows=[self.record('А',[1,2],[1,2]),self.record('Б',[3,4],[3,4],unit='м')]
        config=self.config(totals={'row_levels':['building'],'column_levels':[],'row_grand':True,'column_grand':True,'position':'before'})
        grid=build_layout(rows,self.report,config)
        self.assertEqual(grid['rows'][1]['kind'],'subtotal')
        self.assertEqual({r['unit'] for r in grid['rows'] if r['kind']=='grand' and r['measure']=='plan'},{'шт','м'})
