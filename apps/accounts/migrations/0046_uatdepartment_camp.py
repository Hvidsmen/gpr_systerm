import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0045_onecconnection_uat_sync_enabled_and_more'),
        ('personnel', '0055_alter_arrivalpreparation_camp_alter_camp_is_active_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='uatdepartment',
            name='camp',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='uat_departments',
                to='personnel.camp',
                verbose_name='Местонахождение портала',
            ),
        ),
    ]
