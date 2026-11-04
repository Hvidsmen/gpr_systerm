from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill(apps,schema_editor):
    Request=apps.get_model('personnel','MovementReviewRequest')
    for row in Request.objects.select_related('event').all():
        event=row.event
        row.person_id=event.person_id;row.kind=event.kind;row.movement_date=event.date
        row.from_camp_id=event.from_camp_id;row.to_camp_id=event.to_camp_id
        row.departure_destination=event.departure_destination;row.requested_by_id=event.recorded_by_id
        row.save(update_fields=['person','kind','movement_date','from_camp','to_camp','departure_destination','requested_by'])


class Migration(migrations.Migration):
    dependencies=[('personnel','0020_movement_review_details'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.AddField(model_name='movementreviewrequest',name='departure_destination',field=models.CharField(blank=True,max_length=10)),
        migrations.AddField(model_name='movementreviewrequest',name='from_camp',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='+',to='personnel.camp')),
        migrations.AddField(model_name='movementreviewrequest',name='kind',field=models.CharField(blank=True,choices=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд'),('TRANSFER','Перевод'),('CANCEL','Отмена записи')],max_length=12,verbose_name='Действие')),
        migrations.AddField(model_name='movementreviewrequest',name='movement_date',field=models.DateField(null=True,verbose_name='Дата заезда / выезда')),
        migrations.AddField(model_name='movementreviewrequest',name='person',field=models.ForeignKey(null=True,on_delete=django.db.models.deletion.PROTECT,related_name='movement_review_requests',to='personnel.person')),
        migrations.AddField(model_name='movementreviewrequest',name='requested_by',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='movementreviewrequest',name='to_camp',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='+',to='personnel.camp')),
        migrations.AlterField(model_name='movementreviewrequest',name='event',field=models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='review_request',to='personnel.housingevent')),
        migrations.RunPython(backfill,migrations.RunPython.noop),
        migrations.AlterField(model_name='movementreviewrequest',name='kind',field=models.CharField(choices=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд'),('TRANSFER','Перевод'),('CANCEL','Отмена записи')],max_length=12,verbose_name='Действие')),
        migrations.AlterField(model_name='movementreviewrequest',name='movement_date',field=models.DateField(verbose_name='Дата заезда / выезда')),
        migrations.AlterField(model_name='movementreviewrequest',name='person',field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='movement_review_requests',to='personnel.person')),
        migrations.AlterModelOptions(name='movementreviewrequest',options={'ordering':['status','-movement_date','-pk']}),
    ]
