from django.test import TestCase
from django.core.exceptions import ValidationError
from . import test_estimate_review as fixtures,estimate_review as service
from .global_services import GlobalPlanService


class LegacyEstimatorSignatureTests(TestCase):
    setUpTestData=classmethod(fixtures.EstimateReviewTests.setUpTestData.__func__)
    create=fixtures.EstimateReviewTests.create
    prepare=fixtures.EstimateReviewTests.prepare

    def submitted_before_scoping_update(self):
        self.prepare()
        self.version=GlobalPlanService.transition(self.version,self.planner,'submit')
        review=self.version.review_rounds.get()
        old_signature=service.signature(self.version,legacy=True)
        self.assertNotEqual(old_signature,service.signature(self.version))
        # Fixture for a round persisted by the pre-monthly-scope release.
        type(review).objects.filter(pk=review.pk).update(input_fingerprint=old_signature)
        self.frozen=review.snapshot
        return review

    def check_all(self):
        service.review_selected(self.estimator,self.task,service.work_ids(self.version))

    def test_unchanged_old_round_accepts_estimator_confirmation(self):
        review=self.submitted_before_scoping_update()
        self.check_all()
        service.finish(self.estimator,self.task,True)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'APPROVED')
        review.refresh_from_db();self.assertEqual(review.snapshot,self.frozen)
        self.assertTrue(review.decisions.filter(section='ESTIMATE',action='APPROVE').exists())

    def test_old_round_still_rejects_changed_quantity(self):
        self.submitted_before_scoping_update()
        row=self.version.work_allocations.first()
        type(row).objects.filter(pk=row.pk).update(quantity=row.quantity+1)
        self.check_all()
        with self.assertRaisesMessage(ValidationError,'Справочник или план изменился'):
            service.finish(self.estimator,self.task,True)
        self.task.refresh_from_db();self.assertEqual(self.task.status,'OPEN')

    def test_old_round_still_rejects_changed_catalog(self):
        self.submitted_before_scoping_update()
        type(self.simple).objects.filter(pk=self.simple.pk).update(name='Changed externally')
        self.check_all()
        with self.assertRaisesMessage(ValidationError,'Справочник или план изменился'):
            service.finish(self.estimator,self.task,True)

    def test_unknown_fingerprint_is_not_accepted(self):
        review=self.submitted_before_scoping_update()
        type(review).objects.filter(pk=review.pk).update(input_fingerprint='0'*64)
        self.check_all()
        with self.assertRaises(ValidationError):service.finish(self.estimator,self.task,True)
