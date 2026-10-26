from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('works','0016_projectworkitem_cost_share')]
    operations=[migrations.CreateModel(name='MeasurementUnitAlias',fields=[
        ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
        ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
        ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
        ('symbol',models.CharField(max_length=50,verbose_name='Прежнее обозначение')),
        ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
        ('target',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='aliases',to='works.measurementunit')),
    ],options={'constraints':[models.UniqueConstraint(fields=('company','symbol'),name='unique_company_unit_alias')]})]
