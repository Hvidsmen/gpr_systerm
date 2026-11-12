from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('production','0044_production_edit_lock'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(
        name='ProductionBatchReceipt',
        fields=[
            ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
            ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
            ('id',models.UUIDField(editable=False,primary_key=True,serialize=False)),
            ('date',models.DateField()),
            ('digest',models.CharField(max_length=64)),
            ('result',models.JSONField(default=dict)),
            ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
            ('construction_object',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='projects.constructionobject')),
            ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to=settings.AUTH_USER_MODEL)),
        ],
        options={'indexes':[models.Index(fields=['company','created_at'],name='prod_batch_receipt_age')]},
    )]
