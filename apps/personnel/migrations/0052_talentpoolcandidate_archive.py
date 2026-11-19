from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0051_movementreviewrequest_pool_candidate'),
    ]

    operations = [
        migrations.AddField(
            model_name='talentpoolcandidate',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Перенесён в архив'),
        ),
        migrations.AddField(
            model_name='talentpoolcandidate',
            name='archived_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Перенёс в архив',
            ),
        ),
    ]
