from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0005_lodging_places_and_history')]
    operations=[
        migrations.AddField(model_name='shiftpattern',name='on_days',field=models.PositiveSmallIntegerField(default=0,verbose_name='Дней на вахте')),
        migrations.AddField(model_name='shiftpattern',name='off_days',field=models.PositiveSmallIntegerField(default=0,verbose_name='Дней межвахтового отдыха')),
        migrations.AddField(model_name='shiftpattern',name='cycle_codes',field=models.JSONField(blank=True,default=list,verbose_name='Дни повторяющегося цикла')),
    ]
