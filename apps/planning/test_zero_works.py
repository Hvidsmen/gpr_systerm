from types import SimpleNamespace
from django.test import SimpleTestCase
from .zero_works import zero_work


class ZeroWorkTests(SimpleTestCase):
    def test_protect_nonzero_month_subworks_and_days(self):
        def row(q=0,items=None,days=None):
            return SimpleNamespace(quantity=q,item_quantities=items or {},daily_override=days)
        self.assertTrue(zero_work(row()))
        self.assertFalse(zero_work(row(q=1)))
        self.assertFalse(zero_work(row(items={'1':'0.001'})))
        self.assertFalse(zero_work(row(days=[{'quantity':'2'}])))
        self.assertFalse(zero_work(row(days=[{'quantity':'0','items':[{'quantity':'1'}]}])))
        self.assertTrue(zero_work(row(items={'1':'0'},days=[{'quantity':'0'}])))


from django.test import TestCase
from django.urls import reverse
from . import test_workspace as fixtures


class ZeroWorkEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.WorkspaceTests.setUpTestData.__func__(cls)

    def test_cleanup_saves_month_and_preserves_other_months_and_catalog(self):
        workspace=fixtures.WorkspaceTests.create(self)
        version=workspace.baseline_version
        row=version.work_allocations.get(month=fixtures.FEB)
        self.client.force_login(self.planner)
        response=self.client.post(reverse('planning:workspace_edit',args=[version.pk])+'?month=2026-02-01',{
            'works-TOTAL_FORMS':1,'works-INITIAL_FORMS':1,'works-MIN_NUM_FORMS':0,'works-MAX_NUM_FORMS':1000,
            'works-0-id':row.pk,'works-0-work':self.simple.pk,'works-0-month':'2026-02-01','works-0-quantity':0,'works-0-load_profile':'',
            'resources-TOTAL_FORMS':0,'resources-INITIAL_FORMS':0,'resources-MIN_NUM_FORMS':0,'resources-MAX_NUM_FORMS':1000,'action':'remove_zero_works'})
        self.assertEqual(response.status_code,302)
        self.assertFalse(version.work_allocations.filter(pk=row.pk).exists())
        self.assertTrue(version.work_allocations.filter(month=fixtures.JAN,quantity=300).exists())
        self.assertTrue(type(self.simple).objects.filter(pk=self.simple.pk).exists())

    def test_distribution_error_can_create_missing_future_work(self):
        from unittest.mock import patch
        from django.core.exceptions import ValidationError
        from .workspace_services import WorkspaceService
        from .models import WorkMonthAllocation
        workspace=fixtures.WorkspaceTests.create(self)
        version=WorkspaceService.new_version(workspace,self.planner,'FORECAST',fixtures.FEB,'REMAINING')
        row=WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.simple,month=fixtures.FEB,quantity=220)
        self.client.force_login(self.planner)
        error=ValidationError('Распределите вручную остаток 530.',code='manual_work_remainder',params={'work_id':self.simple.pk,'name':self.simple.name,'remaining':'530','unit':self.simple.unit,'months':['2026-03-01']})
        with patch.object(WorkspaceService,'refresh',side_effect=error):
            response=self.client.post(reverse('planning:workspace_edit',args=[version.pk])+'?month=2026-02-01',{
                'works-TOTAL_FORMS':1,'works-INITIAL_FORMS':1,'works-MIN_NUM_FORMS':0,'works-MAX_NUM_FORMS':1000,
                'works-0-id':row.pk,'works-0-work':self.simple.pk,'works-0-month':'2026-02-01','works-0-quantity':220,'works-0-load_profile':'',
                'resources-TOTAL_FORMS':0,'resources-INITIAL_FORMS':0,'resources-MIN_NUM_FORMS':0,'resources-MAX_NUM_FORMS':1000,'action':'preview'})
        self.assertEqual(response.status_code,302)
        editor=self.client.get(response.url)
        self.assertContains(editor,'Добавить работу для распределения остатка')
        add=reverse('planning:workspace_remaining_work',args=[version.pk])
        self.assertEqual(self.client.post(add,{'month':'2026-01-01'}).status_code,404)
        response=self.client.post(add,{'month':'2026-03-01'})
        self.assertEqual(response.status_code,302)
        manual=version.work_allocations.get(month=fixtures.MAR,work=self.simple)
        self.assertEqual(manual.quantity,0)
        self.assertIn('focus_work=',response.url)
        manual.quantity=530;manual.save()
        self.client.post(add,{'month':'2026-03-01'})
        manual.refresh_from_db();self.assertEqual(manual.quantity,530)
        self.assertEqual(version.work_allocations.filter(month=fixtures.MAR).count(),1)
