from django.db import migrations, models


def backfill_clearance_ready(apps, schema_editor):
    MovementReviewRequest = apps.get_model('personnel', 'MovementReviewRequest')
    MovementReviewRequest.objects.exclude(clearance_document='').update(clearance_ready=True)


class Migration(migrations.Migration):

    dependencies = [
        ('personnel', '0042_movementreviewrequest_requested_movement_date'),
    ]

    operations = [
        migrations.AddField(
            model_name='movementreviewrequest',
            name='clearance_ready',
            field=models.BooleanField(default=False, verbose_name='Обходной лист имеется'),
        ),
        migrations.RunPython(backfill_clearance_ready, migrations.RunPython.noop),
    ]
