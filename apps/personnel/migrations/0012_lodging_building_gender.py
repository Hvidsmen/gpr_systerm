from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('personnel', '0011_move_wagon_description_from_bed_notes')]
    operations = [migrations.AddField(
        model_name='lodgingbuilding', name='gender',
        field=models.CharField(blank=True, choices=[('', 'Не указан'), ('M', 'Мужской'), ('F', 'Женский'), ('MIXED', 'Смешанный')], default='', max_length=5, verbose_name='Пол проживающих'),
    )]
