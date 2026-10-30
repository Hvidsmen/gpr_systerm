from calendar import monthrange
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User,Role
from apps.works.models import ProjectWork,WorkPrice
from . import test_estimate_review as fixtures,estimate_review as service
from .models import WorkMonthAllocation,WorkTask
from .independent_smg import create
from .daily_schedule_table import add_work_branch
from .preparation import handoff


def prepare_future(test):
    test.prepare()
    month=(timezone.localdate().replace(day=1)+timedelta(days=32)).replace(day=1)
    end=month.replace(day=monthrange(month.year,month.month)[1])
    type(test.version.workspace).objects.filter(pk=test.version.workspace_id).update(start_date=month,end_date=end)
    type(test.version).objects.filter(pk=test.version.pk).update(start_date=month,end_date=end)
    test.version.refresh_from_db()
    test.version.work_allocations.filter(month=fixtures.fixtures.JAN).update(month=month)
    test.day=month
    test.second=ProjectWork.objects.create(company=test.company,section=test.simple.section,name='Вторая работа',unit=test.simple.unit,unit_price=20,load_profile=test.simple.load_profile)
    WorkMonthAllocation.objects.create(company=test.company,version=test.version,work=test.second,month=month,quantity=10)


class PriceReviewBatchTests(TestCase):
    setUpTestData=classmethod(fixtures.EstimateReviewTests.setUpTestData.__func__)
    create=fixtures.EstimateReviewTests.create
    prepare=fixtures.EstimateReviewTests.prepare

    def setUp(self):
        prepare_future(self)
        self.url=reverse('planning:estimate_review_detail',args=[self.task.pk])
        self.client.force_login(self.estimator)

    def data(self,changes=None):
        return {'action':'save_prices','effective_from':str(self.day),'reason':'Согласованная расценка','snapshot':service.price_edit_token(self.estimator,self.task),'changes':json.dumps(changes or [{'work_id':self.simple.pk,'price':'12.50'},{'work_id':self.second.pk,'price':'25'}])}

    def test_batch_saves_prices_without_names_or_plan_changes_and_returns_live_state(self):
        allocations=list(self.version.work_allocations.values());name=self.simple.name
        response=self.client.post(self.url,self.data(),HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200,response.content)
        result=response.json();self.assertTrue(result['ok']);self.assertEqual(len(result['review']['rows']),2)
        self.assertEqual({row['price'] for row in result['review']['rows']},{'12.50','25.00'})
        self.assertTrue(result['review']['snapshot'])
        self.simple.refresh_from_db();self.assertEqual(self.simple.name,name)
        self.assertEqual(list(self.version.work_allocations.values()),allocations)
        self.assertEqual(WorkPrice.objects.filter(work__in=[self.simple,self.second],effective_from=self.day).count(),2)
        result=self.client.post(self.url,{'action':'bulk_approve','selected':[self.simple.pk,self.second.pk]},HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertEqual(result['review']['checked'],2)
        result=self.client.post(self.url,{'action':'approve'},HTTP_X_REQUESTED_WITH='XMLHttpRequest').json()
        self.assertEqual(result['review']['task_status'],'APPROVED');self.assertFalse(result['review']['can_review'])

    def test_one_invalid_row_or_missing_basis_saves_nothing(self):
        before=WorkPrice.objects.count()
        payload=self.data([{'work_id':self.simple.pk,'price':'12'},{'work_id':self.second.pk,'price':'-1'}])
        response=self.client.post(self.url,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,400);self.assertIn(str(self.second.pk),response.json()['errors'])
        self.assertEqual(WorkPrice.objects.count(),before)
        payload=self.data();payload['reason']='';payload['effective_from']=''
        result=self.client.post(self.url,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(result.status_code,400);self.assertIn('reason',result.json()['errors']);self.assertIn('effective_from',result.json()['errors'])
        self.assertEqual(WorkPrice.objects.count(),before)

    def test_late_price_validation_error_rolls_back_earlier_rows(self):
        old=self.day-timedelta(days=60)
        WorkPrice.objects.create(company=self.company,work=self.simple,price=10,effective_from=old,created_by=self.planner,reason='Старая цена')
        before=WorkPrice.objects.count();payload=self.data();payload['effective_from']=str(old)
        response=self.client.post(self.url,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,400);self.assertEqual(WorkPrice.objects.count(),before)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'OPEN')

    def test_stale_and_other_actor_tokens_do_not_overwrite_prices(self):
        payload=self.data();self.second.name='Изменена в другой вкладке';self.second.save()
        response=self.client.post(self.url,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest');self.assertEqual(response.status_code,400)
        other=User.objects.create_user(username='price-batch-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        payload=self.data();payload['snapshot']=service.price_edit_token(other,self.task)
        self.assertEqual(self.client.post(self.url,payload,HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code,403)
        self.assertFalse(WorkPrice.objects.filter(work=self.simple,effective_from=self.day).exists())

    def test_only_price_actions_are_exposed_and_structural_posts_and_services_are_denied(self):
        page=self.client.get(self.url);self.assertContains(page,'data-review-price-input');self.assertContains(page,'Сохранить цены')
        for forbidden in ['Изменить название','Добавить работу в составе','Заменить дубль','composite_setup','recommended_work']:
            self.assertNotContains(page,forbidden)
        before=list(self.version.work_allocations.values())
        for action in ['item','replace','bulk_merge','composite_setup','composite_confirm']:
            self.assertEqual(self.client.post(self.url,{'action':action,'work_id':self.simple.pk}).status_code,403)
        self.assertEqual(self.client.post(self.url,{'action':'change','work_id':self.simple.pk,'name':'Подмена','price':12,'effective_from':str(self.day),'reason':'Тест'}).status_code,403)
        for call in [lambda:service.change(self.estimator,self.task,self.simple.pk,name='Подмена'),lambda:service.change(self.estimator,self.task,self.simple.pk,item_data={'name':'Подмена'}),lambda:service.replace_work(self.estimator,self.task,self.simple.pk,self.second.pk),lambda:service.merge_duplicates(self.estimator,self.task,[self.simple.pk,self.second.pk],self.simple.pk),lambda:service.merge_composite(self.estimator,self.task,[self.simple.pk,self.second.pk],{})]:
            with self.assertRaises(PermissionDenied):call()
        self.assertEqual(list(self.version.work_allocations.values()),before)

    def test_smg_confirmation_updates_prices_but_preserves_captured_work_structure(self):
        schedule=create(self.planner,self.obj,self.day,name='Проверка цены')
        entry=add_work_branch(self.planner,schedule,'package:'+str(self.simple.pk))[0]
        old_label=entry.label;old_metadata=deepcopy(entry.metadata);old_rows=[{k:v for k,v in row.items() if k!='value'} for row in entry.rows]
        handoff(self.planner,schedule,self.manager,self.estimator);task=schedule.estimate_reviews.order_by('-pk').first()
        service.change_prices(self.estimator,task,[{'work_id':self.simple.pk,'price':'30'}],effective_from=self.day,reason='Цена СМГ',snapshot=service.price_edit_token(self.estimator,task))
        service.review_work(self.estimator,task,self.simple.pk,True);service.finish(self.estimator,task,True)
        entry.refresh_from_db();task.refresh_from_db()
        self.assertEqual(entry.label,old_label)
        self.assertEqual({k:v for k,v in entry.metadata.items() if k not in {'unit_price','revenue_prices'}},{k:v for k,v in old_metadata.items() if k not in {'unit_price','revenue_prices'}})
        self.assertEqual([{k:v for k,v in row.items() if k!='value'} for row in entry.rows],old_rows)
        work=self.simple;self.assertEqual(task.decisions[str(work.pk)]['signature'],service.decision_signature(work,schedule))
