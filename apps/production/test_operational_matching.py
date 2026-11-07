from datetime import timedelta
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.works.models import ComplexObject, ProjectWork
from . import test_production_entry as fixtures
from .models import DailyFact, OperationalWorkMatch, OperationalWorkMatchRule
from .smg_period_report import build


class OperationalWorkMatchingTests(TestCase):
    setUpTestData=classmethod(fixtures.ProductionEntryTests.setUpTestData.__func__)

    def setUp(self):
        self.planner=User.objects.create_user(username='matching-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.planner.assigned_objects.add(self.obj);self.client.force_login(self.planner)
        complex_object=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.work.section,name='Прочее',allow_dispatcher_work_items=True)
        self.package=ProjectWork.objects.create(company=self.company,section=self.work.section,complex_object=complex_object,name='Пакет прочих работ',kind='COMPOSITE',unit='компл.')
        self.target=ProjectWork.objects.create(company=self.company,section=self.work.section,name='Прокладка кабеля',kind='SIMPLE',unit='м.')
        self.fact=DailyFact.objects.create(company=self.company,project_work=self.package,date=self.day,operational_key=uuid4(),operational_name='Прокладка кабеля временная',operational_unit='м.',actual_quantity=11,reported_by=self.users['DISPATCHER'])
        self.url=reverse('production:operational_matches')

    def test_planner_matches_fact_and_report_groups_it_as_catalogue_work(self):
        catalogue=self.client.get(reverse('works:work_list'));self.assertContains(catalogue,'Прочие работы')
        day=self.client.get(reverse('production:fact_day_workspace'),{'construction_object':self.obj.pk,'date':self.day,'section':'works'})
        self.assertEqual(day.status_code,200);self.assertContains(day,f'?fact={self.fact.pk}')
        page=self.client.get(self.url,{'fact':self.fact.pk});self.assertEqual(page.status_code,200);self.assertContains(page,'Прокладка кабеля временная')
        report_url=reverse('production:smg_period_detail')
        report_query={'construction_object':self.obj.pk,'date':self.day,'scope':'day'}
        before=self.client.get(report_url,report_query);self.assertContains(before,f'?fact={self.fact.pk}');self.assertContains(before,'Сопоставить')
        response=self.client.post(self.url,{'fact':self.fact.pk,'target':f'work:{self.target.pk}'})
        self.assertRedirects(response,self.url)
        match=OperationalWorkMatch.objects.get(fact=self.fact);self.assertEqual((match.target_work,match.matched_by),(self.target,self.planner))
        rows=build(self.obj,self.day,day_only=True)['works']
        target=next(row for row in rows if row['label']=='Прокладка кабеля')
        self.assertEqual(target['actual'],11)
        self.assertFalse(any(row['label']=='Прокладка кабеля временная' for row in rows))
        after=self.client.get(report_url,report_query);self.assertContains(after,'Прокладка кабеля временная');self.assertContains(after,'Изменить')
        matrix=self.client.get(report_url,{**report_query,'by_day':'1'});self.assertContains(matrix,f'?fact={self.fact.pk}')
        self.client.force_login(self.users['DISPATCHER'])
        dispatcher=self.client.get(report_url,report_query);self.assertContains(dispatcher,'Прокладка кабеля временная');self.assertNotContains(dispatcher,f'?fact={self.fact.pk}')

    def test_group_and_future_rule_match_same_object_name_without_unit_check(self):
        second=DailyFact.objects.create(company=self.company,project_work=self.package,date=self.day-timedelta(days=1),operational_key=uuid4(),operational_name=self.fact.operational_name,operational_unit='шт.',actual_quantity=2)
        self.client.post(self.url,{'fact':self.fact.pk,'target':f'work:{self.target.pk}','apply_group':'1','remember_future':'1'})
        self.assertEqual(OperationalWorkMatch.objects.filter(fact__in=[self.fact,second]).count(),2)
        self.assertEqual(OperationalWorkMatchRule.objects.count(),1)
        future=DailyFact.objects.create(company=self.company,project_work=self.package,date=self.day+timedelta(days=1),operational_key=uuid4(),operational_name=self.fact.operational_name,operational_unit='рейс',actual_quantity=3)
        self.assertEqual(future.operational_match.target_work,self.target)

    def test_permissions_are_enforced_but_different_units_are_allowed_without_conversion(self):
        self.client.force_login(self.users['DISPATCHER']);self.assertEqual(self.client.get(self.url).status_code,403)
        self.client.force_login(self.planner)
        wrong=ProjectWork.objects.create(company=self.company,section=self.work.section,name='Штучная работа',kind='SIMPLE',unit='шт.')
        self.client.post(self.url,{'fact':self.fact.pk,'target':f'work:{wrong.pk}'})
        self.assertEqual(OperationalWorkMatch.objects.get(fact=self.fact).target_work,wrong)
        row=next(row for row in build(self.obj,self.day,day_only=True)['works'] if row['label']=='Штучная работа')
        self.assertEqual((row['actual'],row['unit']),(11,'шт.'))
