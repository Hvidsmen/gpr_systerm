from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('personnel','0018_movement_recommendation_override'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(
        name='MovementReviewRequest',
        fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
            ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
            ('status',models.CharField(choices=[('OPEN','Ожидает проверки'),('ACCEPTED','Принято')],default='OPEN',max_length=10,verbose_name='Статус')),
            ('reviewed_at',models.DateTimeField(blank=True,null=True)),
            ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
            ('event',models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,related_name='review_request',to='personnel.housingevent')),
            ('reviewed_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
        ],
        options={'ordering':['status','-event__date','-pk']},
    )]
