from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('personnel','0057_remove_lodging_departure_date_constraint'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(
            name='LodgingSyncState',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('day',models.DateField()),('source_version',models.PositiveBigIntegerField(default=1)),
                ('calculated_version',models.PositiveBigIntegerField(default=0)),('issue_count',models.PositiveIntegerField(default=0)),
                ('status',models.CharField(choices=[('IDLE','Ожидает расчёта'),('QUEUED','Задание принято'),('RUNNING','Выполняется'),('DONE','Завершено'),('ERROR','Ошибка')],default='IDLE',max_length=10)),
                ('stage',models.CharField(blank=True,max_length=80)),('progress',models.PositiveSmallIntegerField(default=0)),
                ('error',models.TextField(blank=True)),('result',models.JSONField(blank=True,default=list)),
                ('calculated_at',models.DateTimeField(blank=True,null=True)),('started_at',models.DateTimeField(blank=True,null=True)),('finished_at',models.DateTimeField(blank=True,null=True)),
                ('camp',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='lodging_sync_states',to='personnel.camp')),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
                ('requested_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(model_name='lodgingsyncstate',constraint=models.UniqueConstraint(fields=('company','camp','day'),name='unique_lodging_sync_state')),
        migrations.AddIndex(model_name='lodgingsyncstate',index=models.Index(fields=['company','camp','day'],name='lodging_sync_scope')),
        migrations.AddIndex(model_name='lodgingstay',index=models.Index(condition=models.Q(('cancelled_at',None),('departed_on',None)),fields=['company','camp'],name='lodging_open_camp')),
        migrations.AddIndex(model_name='lodgingstay',index=models.Index(condition=models.Q(('cancelled_at',None),('departed_on',None)),fields=['company','employee'],name='lodging_open_employee')),
        migrations.AddIndex(model_name='lodgingstay',index=models.Index(condition=models.Q(('cancelled_at',None),('departed_on',None)),fields=['company','bed'],name='lodging_open_bed')),
        migrations.AddIndex(model_name='lodgingevent',index=models.Index(fields=['company','camp','date'],name='lodging_event_camp_day')),
        migrations.AddIndex(model_name='lodgingevent',index=models.Index(fields=['company','camp','created_at'],name='lodging_event_camp_time')),
    ]
