from copy import deepcopy
from django.db import migrations


def migrate_reviews(apps, schema_editor):
    Version = apps.get_model('planning', 'GlobalPlanVersion')
    Review = apps.get_model('planning', 'GlobalPlanReview')
    Decision = apps.get_model('planning', 'GlobalPlanDecision')
    alias = schema_editor.connection.alias
    for version in Version.objects.using(alias).filter(status__in=['SUBMITTED', 'APPROVED', 'COMPLETED'], approval_round=0):
        review = Review.objects.using(alias).create(company_id=version.company_id, version_id=version.pk,
            number=1, snapshot=deepcopy(version.snapshot), submitted_by_id=version.created_by_id,
            legacy_approved=version.status in ['APPROVED', 'COMPLETED'])
        Version.objects.using(alias).filter(pk=version.pk).update(approval_round=1)
        if review.legacy_approved:
            Decision.objects.using(alias).create(company_id=version.company_id, review_id=review.pk,
                section='CEO', action='APPROVE', actor_id=version.approved_by_id,
                actor_name='Согласование до введения нового маршрута', actor_role='LEGACY',
                comment='Прежнее утверждение сохранено. Согласования служб задним числом не назначались.')
    for version in Version.objects.using(alias).filter(version_kind='FORECAST', workspace__isnull=False, baseline_review__isnull=True):
        base_id = version.workspace.baseline_version_id
        review = Review.objects.using(alias).filter(version_id=base_id, legacy_approved=True).first()
        if review:
            Version.objects.using(alias).filter(pk=version.pk).update(baseline_review_id=review.pk)


class Migration(migrations.Migration):
    dependencies = [('accounts', '0004_approval_roles'), ('planning', '0010_globalplanversion_approval_round_globalplanreview_and_more')]
    operations = [migrations.RunPython(migrate_reviews, migrations.RunPython.noop)]
