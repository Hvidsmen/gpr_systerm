from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('planning','0046_table_resource_links'),('works','0017_measurement_unit_alias')]
    operations=[
        migrations.CreateModel(name='SubobjectWorkOrigin',fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
            ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
            ('item_mapping',models.JSONField(blank=True,default=dict)),
            ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
            ('source_work',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='subobject_origins',to='works.projectwork')),
            ('local_work',models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,related_name='gpr_origin',to='works.projectwork')),
        ]),
        migrations.AddField(model_name='dailyscheduleentry',name='gpr_work',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='subobject_smg_entries',to='works.projectwork',verbose_name='Работа общего ГПР')),
    ]
