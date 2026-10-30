from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.core.exceptions import ValidationError,PermissionDenied
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from apps.projects.models import ConstructionObject,Section
from apps.planning.models import SubobjectWorkOrigin
from apps.production.models import DailyFact
from .models import ProjectWork,ProjectWorkItem,WorkPrice
from .test_schedule_sync import CatalogueSyncTests
from .schedule_sync import apply_catalogue
from . import propagation as service
from .structure import structure_revision

class PropagationTests(TestCase):
    setUp=CatalogueSyncTests.setUp
    def seed(self):
        child=ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=self.obj,name='Субобъект')
        grand=ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=child,name='Вложенный')
        sibling=ConstructionObject.objects.create(company=self.company,project=self.obj.project,parent=self.obj,name='Соседний')
        self.user.assigned_objects.add(child,grand,sibling)
        rows=CatalogueSyncTests.rows_with_sections(self)
        apply_catalogue(self.user,self.obj,self.profile,rows)
        return [ProjectWork.objects.get(section__construction_object=obj,is_legacy_structure=False) for obj in [self.obj,child,grand,sibling]]
    def payload(self,work,changes):
        plans,digest,changes=service.prepare(self.user,work,changes)
        return {'user':self.user.pk,'company':self.company.pk,'work':work.pk,'changes':changes,'revision':digest}
    def test_child_changes_only_child_and_nested_and_preview_has_no_writes(self):
        parent,child,grand,sibling=self.seed();item=child.items.get(name='Кабель')
        changes={'items':{str(item.pk):{'quantity_per_unit':'42.000','weight':'55.00'}}}
        payload=self.payload(child,changes)
        self.assertEqual(ProjectWorkItem.objects.get(pk=item.pk).quantity_per_unit,290)
        service.apply(self.user,payload)
        for work in [child,grand]:self.assertEqual(work.items.get(name='Кабель').quantity_per_unit,42)
        for work in [parent,sibling]:self.assertEqual(work.items.get(name='Кабель').quantity_per_unit,290)
        with self.assertRaises(ValidationError):service.apply(self.user,payload)
    def test_locked_history_keeps_old_identity_and_future_edits_follow_new_origins(self):
        works=self.seed();parent=works[0];item=parent.items.get(name='Кабель')
        fact=DailyFact.objects.create(company=self.company,project_work=parent,work_item=item,date='2026-10-01',actual_quantity=3)
        work,count=service.apply(self.user,self.payload(parent,{'items':{str(item.pk):{'quantity_per_unit':'500'}}}))
        self.assertNotEqual(work.pk,parent.pk);parent.refresh_from_db();item.refresh_from_db();fact.refresh_from_db()
        self.assertTrue(parent.is_legacy_structure);self.assertEqual(item.quantity_per_unit,290);self.assertEqual(fact.work_item_id,item.pk)
        current=work.items.get(name='Кабель')
        service.apply(self.user,self.payload(work,{'items':{str(current.pk):{'quantity_per_unit':'600'}}}))
        for child in works[1:]:self.assertEqual(child.items.get(name='Кабель').quantity_per_unit,600)
    def test_preview_requires_confirmation_and_rejects_tampering(self):
        work=self.seed()[0];item=work.items.get(name='Кабель')
        from django.test import RequestFactory
        request=RequestFactory().get('/');request.user=self.user
        self.client.force_login(self.user)
        response=self.client.post(reverse('works:work_structure_update',args=[work.pk]),{'revision':structure_revision(list(work.items.order_by('sequence','pk'))),**{f'{prefix}_{i.pk}':str(value) for i in work.items.all() for prefix,value in [('unit',i.unit),('norm',999),('share',50),('profile',self.profile.pk)]}})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Подтвердить')
        item.refresh_from_db();self.assertEqual(item.quantity_per_unit,290)
        token=response.context['token'];self.assertEqual(self.client.post(reverse('works:work_change_confirm'),{'token':token+'x'}).status_code,400)
        self.assertEqual(self.client.post(reverse('works:work_change_confirm'),{'token':token}).status_code,302)
        item.refresh_from_db();self.assertEqual(item.quantity_per_unit,999)
    def test_stale_target_and_missing_scope_and_atomic_failure(self):
        parent,child,grand,sibling=self.seed();item=parent.items.get(name='Кабель');changes={'items':{str(item.pk):{'quantity_per_unit':'700'}}}
        payload=self.payload(parent,changes);child.name='Обновлён';child.save()
        with self.assertRaises(ValidationError):service.apply(self.user,payload)
        self.user.role=Role.objects.get(code='PLANNER');self.user.save();self.user.assigned_objects.add(self.obj,child.section.construction_object,grand.section.construction_object,sibling.section.construction_object)
        self.user.assigned_objects.remove(grand.section.construction_object)
        with self.assertRaises(PermissionDenied):self.payload(parent,changes)
        self.user.assigned_objects.add(grand.section.construction_object)
        payload=self.payload(parent,changes);original=ProjectWorkItem.full_clean
        def fail(instance,*a,**kw):
            if instance.project_work_id==grand.pk:raise ValidationError('test failure')
            return original(instance,*a,**kw)
        with patch.object(ProjectWorkItem,'full_clean',fail):
            with self.assertRaises(ValidationError):service.apply(self.user,payload)
        for work in [parent,child,grand,sibling]:self.assertEqual(work.items.get(name='Кабель').quantity_per_unit,290)
    def test_dated_price_propagates_down_only_preserving_price_history(self):
        parent,child,grand,sibling=self.seed()
        result,count=service.apply(self.user,self.payload(child,{'price':{'price':'123.45','effective_from':'2026-11-01','reason':'Новый договор','corrects':None}}))
        self.assertEqual(count,2)
        for work in [child,grand]:self.assertEqual(work.price_history.get(effective_from='2026-11-01').price,Decimal('123.45'))
        for work in [parent,sibling]:self.assertFalse(work.price_history.filter(effective_from='2026-11-01').exists())
