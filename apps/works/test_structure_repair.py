from decimal import Decimal as D
from django.test import TestCase
from django.urls import reverse
from .test_merge import WorkMergeTests
from .models import ProjectWork, MeasurementUnit
from .structure_repair import selected_groups
from .merge_service import apply_merge, fingerprint
from apps.production.models import DailyFact
from datetime import date


class StructureRepairTests(TestCase):
    setUpTestData=classmethod(WorkMergeTests.setUpTestData.__func__)
    setUp=WorkMergeTests.setUp

    def test_single_record_and_fact_preserved_as_component(self):
        fact=DailyFact.objects.create(company=self.company,project_work=self.simple,date=date(2026,1,5),actual_quantity=7,reported_by=self.planner)
        values={**self.data,'name':self.simple.name,f'item_name_{self.simple.pk}':self.simple.section.name}
        parent,_=apply_merge(self.planner,[self.simple.pk],values,fingerprint([self.simple]),minimum=1)
        self.assertEqual(parent.name,self.simple.name)
        self.assertEqual(parent.items.get().name,self.simple.section.name)
        fact.refresh_from_db();self.assertEqual(fact.project_work,parent);self.assertEqual(fact.work_item,parent.items.get())
        self.assertEqual(fact.actual_quantity,D(7))
        self.second.refresh_from_db();self.assertFalse(hasattr(self.second,'merged_source'))

    def test_group_by_complex_name_and_one_object(self):
        self.second.name=self.simple.name;self.second.save()
        groups=selected_groups(self.planner,[self.simple.pk,self.second.pk])
        self.assertEqual(len(groups),1)
        self.assertEqual(len(groups[0]),2)

    def test_preview_confirm_and_replay(self):
        MeasurementUnit.objects.get_or_create(company=self.company,symbol='шт',defaults={'name':'Штуки'})
        url=reverse('works:structure_repair')
        response=self.client.post(url,{'selected':[self.simple.pk],'stage':'select'})
        self.assertContains(response,'Исправить структуру выбранных работ')
        data={'selected':[self.simple.pk],'stage':'preview','g0-name':self.simple.name,'g0-section':self.simple.section_id,'g0-unit':'шт','g0-allow_fractional':'on',f'g0-norm_{self.simple.pk}':'1',f'g0-profile_{self.simple.pk}':self.profile.pk,f'g0-item_name_{self.simple.pk}':self.simple.section.name}
        response=self.client.post(url,data)
        self.assertTrue(response.context.get('preview'),response.context['groups'][0]['form'].errors)
        token=response.context['preview']
        response=self.client.post(url,{'stage':'confirm','preview':token})
        self.assertEqual(response.status_code,302)
        parent=ProjectWork.objects.get(items__merge_origin__source_work=self.simple)
        self.assertEqual(parent.items.get().name,self.simple.section.name)
        count=ProjectWork.objects.count()
        self.client.post(url,{'stage':'confirm','preview':token})
        self.assertEqual(ProjectWork.objects.count(),count)

    def test_changed_source_rejects_confirmation(self):
        MeasurementUnit.objects.get_or_create(company=self.company,symbol='шт',defaults={'name':'Штуки'})
        data={'selected':[self.simple.pk],'stage':'preview','g0-name':self.simple.name,'g0-section':self.simple.section_id,'g0-unit':'шт','g0-allow_fractional':'on',f'g0-norm_{self.simple.pk}':'1',f'g0-profile_{self.simple.pk}':self.profile.pk,f'g0-item_name_{self.simple.pk}':self.simple.section.name}
        response=self.client.post(reverse('works:structure_repair'),data)
        token=response.context['preview']
        self.simple.unit_price=12;self.simple.save()
        response=self.client.post(reverse('works:structure_repair'),{'stage':'confirm','preview':token},follow=True)
        self.assertContains(response,'изменились')
        self.assertFalse(hasattr(ProjectWork.objects.get(pk=self.simple.pk),'merged_source'))
