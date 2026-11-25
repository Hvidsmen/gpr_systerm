from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0067_basecity_archived_at_basecity_archived_by_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='staffingslot',
            name='construction_object',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='personnel_assignments', to='projects.constructionobject', verbose_name='Объект привязки'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='assigned_from',
            field=models.DateField(blank=True, null=True, verbose_name='Привязан с'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='assigned_until',
            field=models.DateField(blank=True, null=True, verbose_name='Привязан по (включительно)'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='previous_assignment',
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='next_assignment', to='personnel.staffingslot', verbose_name='Предыдущая привязка'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='assigned_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Привязал'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Привязка удалена'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='cancelled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Удалил привязку'),
        ),
        migrations.AlterField(
            model_name='staffingslot',
            name='cancellation_reason',
            field=models.TextField(blank=True, verbose_name='Комментарий к удалению привязки'),
        ),
    ]
