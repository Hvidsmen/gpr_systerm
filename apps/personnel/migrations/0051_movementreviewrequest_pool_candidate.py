from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0050_cleanup_invalid_recruitment_specialists'),
    ]

    operations = [
        migrations.AddField(
            model_name='movementreviewrequest',
            name='pool_candidate',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='movement_requests',
                to='personnel.talentpoolcandidate',
                verbose_name='Карточка кандидата',
            ),
        ),
    ]
