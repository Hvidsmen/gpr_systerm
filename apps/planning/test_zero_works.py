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
