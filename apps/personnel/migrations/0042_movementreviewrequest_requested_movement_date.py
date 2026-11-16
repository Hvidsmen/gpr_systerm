from django.db import migrations, models


def backfill_requested_movement_date(apps, schema_editor):
    MovementReviewRequest = apps.get_model('personnel', 'MovementReviewRequest')
    MovementReviewRequest.objects.filter(requested_movement_date__isnull=True).update(
        requested_movement_date=models.F('movement_date'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('personnel', '0041_recruitmentcandidate_normalized_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='movementreviewrequest',
            name='requested_movement_date',
            field=models.DateField(blank=True, editable=False, null=True, verbose_name='Плановая дата, указанная в заявке'),
        ),
        migrations.RunPython(backfill_requested_movement_date, migrations.RunPython.noop),
    ]
