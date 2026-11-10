from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[('personnel','0033_movement_request_approval_batch')]
    operations=[
        migrations.AddField(model_name='movementreviewrequest',name='planned_hub_departure_date',field=models.DateField(blank=True,null=True,verbose_name='Плановая дата выезда из ОП Дудинка')),
        migrations.AddField(model_name='movementreviewrequest',name='planned_hub_departure_time',field=models.TimeField(blank=True,null=True,verbose_name='Плановое время выезда из ОП Дудинка')),
    ]
