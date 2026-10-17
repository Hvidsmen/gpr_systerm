from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.accounts.models import User,Role
from apps.projects.models import ConstructionObject,Section
from apps.works.models import ProjectWork
from . import test_macro_history as fixtures,estimate_review
from .estimate_catalog import alternatives
from .models import WorkMonthAllocation,MacroCarryoverWork
from .test_workspace import MAR


class EstimateCatalogTests(TestCase):
    setUpTestData=classmethod(fixtures.MacroHistoryTests.setUpTestData.__func__)
    prepare=fixtures.MacroHistoryTests.prepare
    approve=fixtures.MacroHistoryTests.approve

    def setup_review(self):
        self.prepare()
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=MAR,quantity=40)
        self.estimator=User.objects.create_user(username='macro-catalog-reviewer',company=self.company,role=Role.objects.get(code='ESTIMATOR'))
        self.task=estimate_review.assign(self.planner,self.version,self.estimator)

    def test_connected_catalog_is_visible_with_object_names(self):
        self.setup_review()
        self.child_works[0].name=self.simple.name;self.child_works[0].save()
        self.client.force_login(self.estimator)
        response=self.client.get(reverse('planning:estimate_review_detail',args=[self.task.pk]))
        self.assertEqual(response.status_code,200)
        work=response.context['works'][0]
        self.assertTrue({w.pk for w in self.child_works}<={w.pk for w in work.replacements})
        self.assertContains(response,'КТП-1');self.assertContains(response,'КТП-2')
        self.assertTrue(any(pair['second'].pk==self.child_works[0].pk and pair['catalog'] for pair in response.context['similar_works']))
        self.assertFalse(MacroCarryoverWork.objects.exists())

    def test_child_replacement_is_copied_once_and_preserves_original_history(self):
        self.setup_review()
        source=self.child_works[0]
        estimate_review.replace_work(self.estimator,self.task,self.simple.pk,source.pk)
        mapping=MacroCarryoverWork.objects.get(source_work=source)
        self.assertEqual(mapping.target_work.section.construction_object_id,self.obj.pk)
        self.assertEqual(self.version.work_allocations.get().work_id,mapping.target_work_id)
        self.assertEqual(self.version.work_allocations.get().quantity,40)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.simple,month=MAR,quantity=5)
        estimate_review.replace_work(self.estimator,self.task,self.simple.pk,source.pk)
        self.assertEqual(MacroCarryoverWork.objects.count(),1)
        self.assertEqual(self.version.work_allocations.get().quantity,45)
        for version,snapshot in zip(self.sources,self.original):
            version.refresh_from_db();self.assertEqual(version.snapshot,snapshot)
        source.refresh_from_db();self.assertEqual(source.section.construction_object_id,self.children[0].pk)

    def test_continuation_keeps_connected_catalog_sources(self):
        from datetime import date
        from .continuation import continue_gpr
        self.setup_review();approved=self.approve(self.version)
        workspace=continue_gpr(self.planner,approved,'Next',date(2026,4,30))
        version=workspace.baseline_version
        self.assertTrue(alternatives(version).filter(pk=self.child_works[0].pk).exists())
        WorkMonthAllocation.objects.create(company=self.company,version=version,work=self.simple,month=date(2026,4,1),quantity=8)
        task=estimate_review.assign(self.planner,version,self.estimator)
        estimate_review.replace_work(self.estimator,task,self.simple.pk,self.child_works[0].pk)
        self.assertEqual(version.work_allocations.get().quantity,8)

    def test_replacement_remark_records_child_without_copying(self):
        self.setup_review()
        text=estimate_review.replacement_comment(self.estimator,self.task,self.simple.pk,self.child_works[0].pk,'Взять существующую работу')
        self.assertIn('КТП-1',text)
        self.assertIn(f'ID {self.child_works[0].pk}',text)
        self.assertFalse(MacroCarryoverWork.objects.exists())

    def test_unconnected_child_is_not_allowed_even_with_same_parent(self):
        self.setup_review()
        child=ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=self.obj,name='Unconnected')
        section=Section.objects.create(company=self.company,construction_object=child,name='Other')
        work=ProjectWork.objects.create(company=self.company,section=section,name='Other',unit=self.simple.unit)
        self.assertNotIn(work.pk,set(alternatives(self.version).values_list('pk',flat=True)))
        with self.assertRaises(ValidationError):estimate_review.replace_work(self.estimator,self.task,self.simple.pk,work.pk)
        self.assertEqual(self.version.work_allocations.get().work_id,self.simple.pk)
