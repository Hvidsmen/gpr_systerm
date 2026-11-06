from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('production','0040_production_daily_crews')]
    operations=[
        migrations.AddField(
            model_name='employeeworklink',name='assigned_individually',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='employeeworklink',name='crew_source',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='materialized_employee_links',to='production.productiondailycrew'),
        ),
        migrations.CreateModel(
            name='ProductionDailyCrewWorkLink',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
                ('crew',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='work_assignments',to='production.productiondailycrew')),
                ('fact',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='daily_crew_assignments',to='production.dailyfact')),
            ],
            options={'ordering':['pk']},
        ),
        migrations.AddConstraint(model_name='productiondailycrewworklink',constraint=models.UniqueConstraint(fields=('crew','fact'),name='unique_daily_crew_work_link')),
    ]
