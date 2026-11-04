from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('personnel','0024_person_dispatcher_removal'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='ZupWorkSchedule',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
            ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
            ('source_ref',models.CharField(blank=True,max_length=32,verbose_name='Идентификатор ЗУП')),
            ('name',models.CharField(max_length=250,verbose_name='График ЗУП')),
            ('is_active',models.BooleanField(default=True,verbose_name='Доступен в ЗУП')),
            ('synced_at',models.DateTimeField(blank=True,null=True,verbose_name='Обновлён из ЗУП')),
            ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
        ],options={'ordering':['name','pk']}),
        migrations.CreateModel(name='ScheduleChangeRequest',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
            ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
            ('previous_schedule',models.CharField(blank=True,max_length=250,verbose_name='График до изменения')),
            ('status',models.CharField(choices=[('PENDING','Ожидает изменения в ЗУП'),('APPLIED','Применено из ЗУП'),('CANCELLED','Отменено')],default='PENDING',max_length=12)),
            ('applied_at',models.DateTimeField(blank=True,null=True)),
            ('task_key',models.CharField(blank=True,max_length=180)),
            ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
            ('person',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='schedule_change_requests',to='personnel.person')),
            ('requested_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
            ('requested_schedule',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='change_requests',to='personnel.zupworkschedule')),
        ],options={'ordering':['-created_at','-pk']}),
        migrations.AddConstraint(model_name='zupworkschedule',constraint=models.UniqueConstraint(fields=('company','name'),name='personnel_unique_zup_schedule_name')),
        migrations.AddConstraint(model_name='schedulechangerequest',constraint=models.UniqueConstraint(condition=models.Q(('status','PENDING')),fields=('person',),name='personnel_one_pending_schedule_change')),
    ]
