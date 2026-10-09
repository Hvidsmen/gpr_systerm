from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role
from . import test_batch_prepare as fixtures
from .prices import change_price, price_on
from .batch_prepare import build_preview, apply_batch


class BackdatedPricesTests(TestCase):
    setUpTestData = classmethod(fixtures.BatchPrepareTests.setUpTestData.__func__)

    def setUp(self):
        fixtures.BatchPrepareTests.setUp(self)
        self.past = self.today - timedelta(days=30)

    def administrator(self):
        self.planner.role=Role.objects.get(code='ADMIN')
        self.planner.save(update_fields=['role'])

    def test_admin_can_record_past_price_in_history_and_card(self):
        self.administrator()
        response=self.client.post(reverse('works:price_change',args=[self.simple.pk]),{'price':'77','effective_from':self.past.isoformat(),'reason':'Историческая цена'})
        self.assertEqual(response.status_code,302)
        entry=self.simple.price_history.filter(effective_from=self.past).get()
        self.assertEqual((entry.price,entry.created_by_id),(Decimal(77),self.planner.pk))
        self.assertEqual(price_on(self.simple,self.past),Decimal(77))

    def test_planner_cannot_backdate_card_or_service(self):
        with self.assertRaises(ValidationError):
            change_price(self.planner,self.simple.pk,Decimal(77),self.past)
        response=self.client.post(reverse('works:price_change',args=[self.simple.pk]),{'price':'77','effective_from':self.past.isoformat()})
        self.assertEqual(response.status_code,200)
        self.assertIn('effective_from',response.context['form'].errors)

    def test_admin_excel_preview_and_confirm_accept_past_date(self):
        self.administrator()
        entries=deepcopy(self.entries)
        entries[0][7]='77'
        payload=build_preview(self.planner,self.meta,entries,self.past)
        apply_batch(self.planner,payload)
        work_id=payload['prices'][0]['id']
        from .models import ProjectWork
        work=ProjectWork.objects.get(pk=work_id)
        self.assertEqual(price_on(work,self.past),Decimal(77))
        self.assertEqual(work.price_history.get(effective_from=self.past).created_by_id,self.planner.pk)

    def test_planner_excel_rejects_past_on_preview_and_confirmation(self):
        entries=deepcopy(self.entries);entries[0][7]='77'
        with self.assertRaises(ValidationError):
            build_preview(self.planner,self.meta,entries,self.past)
        payload=build_preview(self.planner,self.meta,entries,self.today)
        payload['effective']=self.past.isoformat()
        with self.assertRaises(ValidationError):
            apply_batch(self.planner,payload)
