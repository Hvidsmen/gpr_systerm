from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('production','0039_production_entry_indexes')]
    operations=[
        migrations.CreateModel(
            name='ProductionDailyCrew',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('date',models.DateField()),('shift',models.PositiveSmallIntegerField(default=1)),('number',models.PositiveIntegerField()),
                ('name',models.CharField(blank=True,max_length=160)),('is_active',models.BooleanField(default=True)),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
                ('construction_object',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='production_daily_crews',to='projects.constructionobject')),
                ('reported_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='reported_production_crews',to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering':['shift','number','pk']},
        ),
        migrations.AddConstraint(model_name='productiondailycrew',constraint=models.UniqueConstraint(fields=('construction_object','date','shift','number'),name='unique_production_daily_crew_number')),
        migrations.CreateModel(
            name='ProductionDailyCrewMember',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
                ('activity',models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,related_name='daily_crew_member',to='production.employeeactivity')),
                ('crew',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='members',to='production.productiondailycrew')),
            ],
            options={'ordering':['activity__employee__last_name','activity__employee__first_name','pk']},
        ),
    ]
