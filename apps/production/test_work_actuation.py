from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from . import test_production_entry as fixtures
from django.test import TestCase
from .models import DailyFact, DayActuation


class WorkActuationTests(TestCase):
    setUpTestData = classmethod(fixtures.ProductionEntryTests.setUpTestData.__func__)
    payload = fixtures.ProductionEntryTests.payload
    row = fixtures.ProductionEntryTests.row

    def test_selected_simple_work_does_not_zero_other_work(self):
        data=self.payload('DISPATCHER','works')
        a=self.row(data,self.work);b=self.row(data,self.second_work)
        data.update({a+'actuation_type':'DAY',a+'actuation_reason':'Мороз',a+'actual_quantity':'9',b+'actual_quantity':'3'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        fact=DailyFact.objects.get(project_work=self.work)
        self.assertEqual((fact.actual_quantity,fact.actual_value),(0,0))
        self.assertEqual(DailyFact.objects.get(project_work=self.second_work).actual_quantity,3)
        self.assertFalse(DayActuation.objects.exists())
        page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'})
        self.assertContains(page,'Актировка этой работы');self.assertContains(page,'data-work-actuation-open')
        self.assertNotContains(page,'Актировка объекта за день')
        from .production_entry import report_data
        self.assertEqual(next(r for r in report_data(self.obj,self.day)['works'] if r['id']==fact.pk)['actuation_reason'],'Мороз')
        from .collaboration import canonical_rows
        row=next(r for r in canonical_rows(self.obj,self.day,report_data(self.obj,self.day))['works'] if r['id']==fact.pk)
        self.assertEqual(row['values']['actuation_type'],'DAY')

    def test_bulk_multiple_work_payload_and_clear_one(self):
        data=self.payload('DISPATCHER','works')
        for work in (self.work,self.second_work):
            prefix=self.row(data,work);data.update({prefix+'actuation_type':'FULL_DAY',prefix+'actuation_reason':'Ветер'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(DailyFact.objects.filter(actuation_type='FULL_DAY',actual_quantity=0).count(),2)
        data=self.payload('DISPATCHER','works');prefix=self.row(data,self.work)
        data.update({prefix+'actuation_type':'',prefix+'actuation_reason':'',prefix+'actual_quantity':'4'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actual_quantity,4)
        self.assertEqual(DailyFact.objects.get(project_work=self.second_work).actuation_type,'FULL_DAY')

    def test_invalid_reason_type_are_atomic(self):
        for kind,reason in [('DAY',''),('DAY','   '),('INVALID','Ветер')]:
            data=self.payload('DISPATCHER','works');a=self.row(data,self.work);b=self.row(data,self.second_work)
            data.update({a+'actuation_type':kind,a+'actuation_reason':reason,b+'actual_quantity':'3'})
            self.assertEqual(self.client.post(self.url,data).status_code,400)
            self.assertFalse(DailyFact.objects.exists())

    def test_direct_model_cannot_save_nonzero_actuated_fact(self):
        with self.assertRaises(ValidationError):
            DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=1,actuation_type='DAY',actuation_reason='Ветер')

    def test_old_client_preserves_work_actuation(self):
        fact=DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=0,actuation_type='DAY',actuation_reason='Ветер')
        from .forms import DailyFactForm
        form=DailyFactForm({'project_work':self.work.pk,'date':str(self.day),'actual_quantity':'0','comment':'Проверено'},instance=fact,user=self.users['DISPATCHER'])
        self.assertTrue(form.is_valid(),form.errors)
        form.save();fact.refresh_from_db();self.assertEqual(fact.actuation_reason,'Ветер')

    def test_legacy_migration_preserves_history_and_scope(self):
        first=DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=0)
        DayActuation.objects.create(company=self.company,construction_object=self.obj,date=self.day,actuation_type='NIGHT',reason='Мороз')
        migration=import_module('apps.production.migrations.0033_work_actuation')
        migration.transfer_object_actuations(apps,SimpleNamespace(connection=connection))
        first.refresh_from_db();self.assertEqual((first.actuation_type,first.actuation_reason),('NIGHT','Мороз'))
        self.assertTrue(DayActuation.objects.exists())
        untouched=DailyFact.objects.create(company=self.company,project_work=self.second_work,date=self.day,actual_quantity=3)
        self.assertEqual(untouched.actuation_type,'')

    def test_stale_actuation_cannot_overwrite_same_row(self):
        data=self.payload('DISPATCHER','works');a=self.row(data,self.work);b=self.row(data,self.second_work)
        data.update({a+'actual_quantity':'5',b+'actual_quantity':'3'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        stale=self.payload('DISPATCHER','works');fresh=self.payload('SECOND','works')
        a=self.row(fresh,self.work);fresh.update({a+'actuation_type':'DAY',a+'actuation_reason':'Ветер'})
        self.assertEqual(self.client.post(self.url,fresh).status_code,302)
        a=self.row(stale,self.work);stale.update({a+'actuation_type':'NIGHT',a+'actuation_reason':'Мороз'})
        self.client.force_login(self.users['DISPATCHER'])
        self.assertEqual(self.client.post(self.url,stale).status_code,400)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actuation_type,'DAY')

    def test_colleague_actuation_does_not_block_other_row(self):
        data=self.payload('DISPATCHER','works');a=self.row(data,self.work);b=self.row(data,self.second_work)
        data.update({a+'actual_quantity':'5',b+'actual_quantity':'3'})
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        stale=self.payload('DISPATCHER','works');fresh=self.payload('SECOND','works')
        a=self.row(fresh,self.work);fresh.update({a+'actuation_type':'DAY',a+'actuation_reason':'Ветер'})
        self.assertEqual(self.client.post(self.url,fresh).status_code,302)
        b=self.row(stale,self.second_work);stale[b+'actual_quantity']='2'
        self.client.force_login(self.users['DISPATCHER'])
        self.assertEqual(self.client.post(self.url,stale).status_code,302)
        self.assertEqual(DailyFact.objects.get(project_work=self.work).actuation_type,'DAY')
        self.assertEqual(DailyFact.objects.get(project_work=self.second_work).actual_quantity,2)
