from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('personnel', '0006_shiftpattern_actual_cycle')]
    operations = [migrations.AlterField(
        model_name='housingevent', name='departure_destination',
        field=models.CharField(blank=True, max_length=10, verbose_name='Куда выезжает', choices=[
            ('REST', 'На межвахтовый отдых'), ('CAMP', 'В другой ВЖГ'), ('DISMISSAL', 'По увольнению'),
        ]),
    )]
