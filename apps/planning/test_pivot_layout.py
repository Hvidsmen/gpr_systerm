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
        subtotal=next(row for row in grid['rows'] if row.get('inline_total') and row['measure']=='plan')
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
        row=next(r for r in grid['rows'] if r['kind']=='group')
        amounts={col['label']:value for col,value in zip(grid['columns'],row['values'])}
        self.assertEqual(amounts['Итого: Комплекс'],D(10));self.assertEqual(amounts['Общий итог'],D(10))
        self.assertEqual(grid['header_depth'],3)
        self.assertEqual(sum(c['colspan'] for c in grid['headers'][0]),len(grid['columns']))
    def test_subtotal_position_is_configurable_and_units_do_not_mix(self):
        rows=[self.record('А',[1,2],[1,2]),self.record('Б',[3,4],[3,4],unit='м')]
        config=self.config(view='table',totals={'row_levels':['building'],'column_levels':[],'row_grand':True,'column_grand':True,'position':'before'})
        grid=build_layout(rows,self.report,config)
        self.assertEqual(grid['rows'][0]['kind'],'subtotal')
        self.assertEqual({r['unit'] for r in grid['rows'] if r['kind']=='grand' and r['measure']=='plan'},{'шт','м'})

    def test_hierarchy_totals_share_group_rows_and_leaves_do_not_repeat_values(self):
        rows=[self.record('А',[1,2],[1,2]),self.record('Б',[3,4],[3,4])]
        grid=build_layout(rows,self.report,self.config(measures=['plan']))
        self.assertEqual([r['label'] for r in grid['rows'] if r['kind']=='group'],['КТП','А','Б'])
        self.assertEqual(grid['rows'][0]['values'],[D(4),D(6),D(10)])
        self.assertFalse(any(r['kind'] in {'subtotal','detail'} for r in grid['rows']))
        parent=grid['rows'][0]
        for row in grid['rows'][1:3]:
            self.assertIn(parent['node_id'],row['ancestors'].split())
            self.assertFalse(row['node_id'])
        self.assertEqual(grid['rows'][-1]['kind'],'grand')

    def test_mixed_units_and_measures_stay_separate_and_all_group_totals_survive_fold(self):
        rows=[self.record('А',[10,20],[5,10]),self.record('Б',[90,80],[90,80]),self.record('В',[5,6],[1,2],unit='м')]
        grid=build_layout(rows,self.report,self.config())
        headers=[r for r in grid['rows'] if r.get('inline_total')]
        self.assertEqual(len(headers),6)
        self.assertEqual(sum(bool(r['node_id']) for r in headers),1)
        self.assertTrue(all(not r['ancestors'] for r in headers))
        self.assertEqual(len([r for r in headers if r['measure']=='completion']),2)
        percent=next(r for r in headers if r['measure']=='completion' and r['indicator']=='Работы · шт')
        self.assertEqual(percent['values'],[D(95),D(90),D('92.5')])
        self.assertEqual({r['unit'] for r in headers if r['measure']=='plan'},{'шт','м'})

    def test_disabled_subtotals_leave_parent_heading_empty_and_keep_leaf_values(self):
        grid=build_layout([self.record('А',[1,2],[1,2])],self.report,self.config(measures=['plan'],totals={}))
        self.assertEqual(grid['rows'][0]['values'],[None,None])
        self.assertEqual(grid['rows'][1]['values'],[D(1),D(2)])
        self.assertFalse(any(r.get('inline_total') or r['kind']=='grand' for r in grid['rows']))

    def test_work_before_complex_objects_has_correct_totals_in_both_views(self):
        rows=[self.record('Одинаковая работа',[10,20],[5,10]),self.record('Одинаковая работа',[90,80],[90,80]),self.record('Одинаковая работа',[5,6],[1,2],unit='м')]
        rows[1]['complex_object']='УЗБ';rows[2]['complex_object']='УЗБ'
        for view in ['hierarchy','table']:
            with self.subTest(view=view):
                config=self.config(rows=['work','complex_object'],view=view,totals={'row_levels':['work'],'column_levels':[],'row_grand':True,'column_grand':True,'position':'after'})
                grid=build_layout(rows,self.report,config)
                groups=[r for r in grid['rows'] if r.get('inline_total') or r['kind']=='subtotal']
                plans={r['unit']:r['values'] for r in groups if r['measure']=='plan'}
                self.assertEqual(plans,{'шт':[D(100),D(100),D(200)],'м':[D(5),D(6),D(11)]})
                ratio=next(r for r in groups if r['measure']=='completion' and r['indicator']=='Работы · шт')
                self.assertEqual(ratio['values'],[D(95),D(90),D('92.5')])
