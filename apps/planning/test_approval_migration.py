from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LegacyApprovalMigrationTests(TransactionTestCase):
    def test_existing_statuses_and_forecast_baseline_are_preserved(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [('planning', '0010_globalplanversion_approval_round_globalplanreview_and_more')]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            Company = apps.get_model('accounts', 'Company')
            Project = apps.get_model('projects', 'Project')
            Object = apps.get_model('projects', 'ConstructionObject')
            Workspace = apps.get_model('planning', 'PlanningWorkspace')
            Version = apps.get_model('planning', 'GlobalPlanVersion')
            company = Company.objects.create(name='Legacy approval migration')
            project = Project.objects.create(company=company, name='Legacy project', code='old')
            obj = Object.objects.create(company=company, project=project, name='Legacy object', code='old')
            day = date(2026, 1, 1)
            workspace = Workspace.objects.create(company=company, construction_object=obj, name='Old plan', start_date=day, end_date=day)
            snapshot = {'works': [], 'resources': {'labor': []}, 'marker':'Original approved snapshot'}
            baseline = Version.objects.create(company=company, construction_object=obj, workspace=workspace, version_kind='BASELINE', version_number=1, start_date=day, end_date=day, status='COMPLETED', snapshot=snapshot)
            workspace.baseline_version = baseline
            workspace.save()
            forecast = Version.objects.create(company=company, construction_object=obj, workspace=workspace, version_kind='FORECAST', planning_month=day, scenario='BASELINE', version_number=2, start_date=day, end_date=day, status='APPROVED', snapshot=snapshot)
            submitted = Version.objects.create(company=company, construction_object=obj, version_number=3, start_date=day, end_date=day, status='SUBMITTED', snapshot=snapshot)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            after = executor.loader.project_state(latest).apps
            Version = after.get_model('planning', 'GlobalPlanVersion')
            Review = after.get_model('planning', 'GlobalPlanReview')
            Decision = after.get_model('planning', 'GlobalPlanDecision')
            migrated_base = Version.objects.get(pk=baseline.pk)
            migrated_forecast = Version.objects.get(pk=forecast.pk)
            migrated_submitted = Version.objects.get(pk=submitted.pk)
            self.assertEqual(migrated_base.status, 'COMPLETED')
            self.assertEqual(migrated_forecast.status, 'APPROVED')
            self.assertEqual(migrated_submitted.status, 'SUBMITTED')
            self.assertEqual(migrated_base.snapshot, snapshot)
            self.assertEqual(migrated_base.approval_round, 1)
            review = Review.objects.get(version_id=baseline.pk)
            self.assertTrue(review.legacy_approved)
            self.assertEqual(review.snapshot, snapshot)
            self.assertEqual(migrated_forecast.baseline_review_id, review.pk)
            self.assertEqual(list(Decision.objects.filter(review=review).values_list('actor_role', flat=True)), ['LEGACY'])
            self.assertFalse(Decision.objects.filter(review__version_id=submitted.pk).exists())
            count = Review.objects.count()
            MigrationExecutor(connection).migrate(before)
            MigrationExecutor(connection).migrate(latest)
            self.assertEqual(Review.objects.count(), count)
            self.assertEqual(Version.objects.get(pk=forecast.pk).baseline_review_id, review.pk)
        finally:
            MigrationExecutor(connection).migrate(latest)
