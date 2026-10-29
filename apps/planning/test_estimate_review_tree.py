from django.test import TestCase
from django.urls import reverse
from apps.works.models import ComplexObject, ProjectWork, ProjectWorkItem
from . import test_estimate_review as fixtures
from .models import WorkMonthAllocation
from .preparation import handoff
from .independent_smg import create
from .daily_schedule_table import add_work_branch
from .test_workspace import JAN


class EstimateReviewTreeTests(TestCase):
    setUpTestData=classmethod(fixtures.EstimateReviewTests.setUpTestData.__func__)
    create=fixtures.EstimateReviewTests.create
    prepare=fixtures.EstimateReviewTests.prepare

    def setUp(self):
        self.prepare()
        self.facility=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.simple.section,name='УЗА 32')
        self.facility2=ComplexObject.objects.create(company=self.company,construction_object=self.obj,section=self.simple.section,name='УЗА 33')
        self.composite.name='Монтаж системы';self.composite.complex_object=self.facility;self.composite.save()
        self.same=ProjectWork.objects.create(company=self.company,section=self.simple.section,complex_object=self.facility2,name='Монтаж системы',kind='COMPOSITE',unit='шт')
        ProjectWorkItem.objects.create(company=self.company,project_work=self.same,name='Прокладка кабеля',unit='м',quantity_per_unit=2,weight=100)
        for work in [self.composite,self.same]:WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=work,quantity=2)
        self.client.force_login(self.estimator)

    def check_tree(self,task):
        page=self.client.get(reverse('planning:estimate_review_detail',args=[task.pk]))
        self.assertEqual(page.status_code,200)
        tree=page.context['review_tree'];self.assertEqual(len(tree),1)
        nodes=tree[0]['objects'];self.assertEqual({node['id'] for node in nodes},{self.facility.pk,self.facility2.pk,None})
        for work,facility in [(self.composite,self.facility),(self.same,self.facility2)]:
            node=next(node for node in nodes if node['id']==facility.pk)
            self.assertEqual([row.pk for row in node['works']],[work.pk])
            self.assertIn(facility.name,node['works'][0].review_search)
        self.assertContains(page,'Раздел → комплексный объект → комплексная работа → работа')
        self.assertContains(page,'data-review-group-select')
        self.assertContains(page,'Прокладка кабеля')
        self.assertEqual(tree[0]['count'],3)
        return page

    def test_gpr_groups_duplicate_names_by_object_identity(self):
        self.check_tree(self.task)

    def test_smg_uses_the_same_hierarchy(self):
        schedule=create(self.planner,self.obj,JAN,name='Иерархия сметчика')
        for token in ['package:'+str(self.simple.pk),'package:'+str(self.composite.pk),'package:'+str(self.same.pk)]:add_work_branch(self.planner,schedule,token)
        handoff(self.planner,schedule,self.manager,self.estimator)
        self.check_tree(schedule.estimate_reviews.order_by('-pk').first())
