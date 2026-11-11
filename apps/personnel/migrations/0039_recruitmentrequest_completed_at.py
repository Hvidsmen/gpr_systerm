from django.db import migrations,models
from django.utils import timezone


def backfill_completed(apps,schema_editor):
    Request=apps.get_model('personnel','RecruitmentRequest')
    for row in Request.objects.filter(cancelled_at__isnull=True):
        arrived=row.candidates.filter(arrival__status='ARRIVED',arrival__actual_stay__isnull=False,
            arrival__actual_stay__cancelled_at__isnull=True).count()
        if arrived>=row.quantity:
            row.completed_at=timezone.now();row.save(update_fields=['completed_at'])


class Migration(migrations.Migration):
    dependencies=[('personnel','0038_movement_recommendation_origin')]
    operations=[migrations.AddField(model_name='recruitmentrequest',name='completed_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.RunPython(backfill_completed,migrations.RunPython.noop)]
