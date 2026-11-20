from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('resources', '0030_backfill_linked_employee_phones'),
    ]

    operations = [
        migrations.AlterField(
            model_name='equipment',
            name='site_name',
            field=models.CharField(blank=True, max_length=150, verbose_name='Исходное местонахождение (импорт)'),
        ),
    ]
