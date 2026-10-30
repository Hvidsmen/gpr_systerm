from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from django.test import TestCase
from django.urls import reverse
from . import test_decomposition as fixtures
from .models import DailySchedule,DailyScheduleEntry,ObjectWorkPlan,ObjectWorkPlanRow,GlobalPlanVersion
from .pivot_engine import DEFAULT

class PivotSourceTests(TestCase):
    setUpTestData=classmethod(fixtures.DecompositionTests.setUpTestData.__func__)
    setUp=fixtures.DecompositionTests.setUp
    def params(self,kind,version):
        return {'plan_type':kind,'plan_version':version.pk,'start':'2026-01-01','end':'2026-01-31'}
    def page(self,params):
        response=self.client.get(reverse('planning:pivot_report_list'),params);self.assertEqual(response.status_code,200);self.assertFalse(response.context['form'].errors,response.context['form'].errors);return response
    def totals(self,response,metric):
        return sum((r['values'][-1] or 0) for r in response.context['grid']['rows'] if r['kind']=='grand' and r['measure']==metric)
    def smg(self,quantity):
        schedule=DailySchedule.objects.create(company=self.company,construction_object=self.obj,month=date(2026,1,1),revision=1,status='APPLIED',created_by=self.planner)
        DailyScheduleEntry.objects.create(schedule=schedule,key='work:'+str(self.simple.pk),kind='work',label=self.simple.name,metadata=next(spec for spec in self.snapshot['works'] if spec['id']==self.simple.pk),rows=[{'date':'2026-01-05','item_id':None,'quantity':str(quantity)}])
        return schedule
    def test_exact_gpr_does_not_mix_smg_and_exact_smg_uses_only_its_rows(self):
        schedule=self.smg(777)
        gpr=self.page(self.params('gpr',self.version));self.assertNotEqual(self.totals(gpr,'plan'),777)
        smg=self.page(self.params('smg',schedule));self.assertEqual(self.totals(smg,'plan'),777)
        self.assertEqual(smg.context['form'].fields['plan_type'].choices,[('gpr','ГПР'),('smg','СМГ'),('object','План на весь объект')])
    def test_whole_object_variant_has_no_invented_calendar_distribution_and_can_export(self):
        plan=ObjectWorkPlan.objects.create(company=self.company,construction_object=self.obj,created_by=self.planner,name='Весь объект')
        ObjectWorkPlanRow.objects.create(company=self.company,plan=plan,work=self.simple,quantity=123)
        response=self.page(self.params('object',plan));self.assertEqual(response.context['pivot_config']['columns'],[]);self.assertEqual(self.totals(response,'plan'),123);self.assertContains(response,'План по месяцам не распределён')
        self.assertEqual(self.client.get(reverse('planning:pivot_report_list'),self.params('object',plan)|{'export':'xlsx'}).status_code,200)
    def test_unknown_version_and_changed_scope_are_rejected(self):
        response=self.client.get(reverse('planning:pivot_report_list'),{'plan_type':'smg','plan_version':'999999'});self.assertIn('plan_version',response.context['form'].errors)
        self.planner.assigned_objects.clear();response=self.client.get(reverse('planning:pivot_report_list'),self.params('gpr',self.version));self.assertIn('plan_version',response.context['form'].errors)
    def test_selected_version_sets_period_if_not_given_and_friendly_months(self):
        response=self.page({'plan_type':'gpr','plan_version':self.version.pk});self.assertEqual(str(response.context['form']['start'].value()),'2026-01-01');self.assertContains(response,'Январь 2026')
