from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[('personnel','0019_movement_review_request')]
    operations=[
        migrations.AddField(model_name='movementreviewrequest',name='base_city',field=models.CharField(blank=True,max_length=150,verbose_name='Базовый город')),
        migrations.AddField(model_name='movementreviewrequest',name='employment_kind',field=models.CharField(blank=True,choices=[('STAFF','Штат'),('NEW','Новый'),('TRANSFER','Перевод'),('BUSINESS_TRIP','Командировка')],max_length=16,verbose_name='Штат / новый / перевод')),
        migrations.AddField(model_name='movementreviewrequest',name='note',field=models.TextField(blank=True,verbose_name='Комментарий / примечание')),
        migrations.AddField(model_name='movementreviewrequest',name='phone',field=models.CharField(blank=True,max_length=80,verbose_name='Номер телефона')),
        migrations.AddField(model_name='movementreviewrequest',name='position_name',field=models.CharField(blank=True,max_length=250,verbose_name='Должность')),
        migrations.AddField(model_name='movementreviewrequest',name='request_number',field=models.CharField(blank=True,max_length=50,verbose_name='№ заявки')),
        migrations.AddField(model_name='movementreviewrequest',name='site',field=models.CharField(blank=True,max_length=250,verbose_name='Участок')),
        migrations.AddField(model_name='movementreviewrequest',name='travel_status',field=models.CharField(blank=True,max_length=80,verbose_name='Статус поездки')),
        migrations.AddField(model_name='movementreviewrequest',name='travel_time',field=models.TimeField(blank=True,null=True,verbose_name='Время прилёта / вылета')),
    ]
