import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('production', '0035_productionlinkreceipt')]

    operations = [
        migrations.AddField(model_name='dailyfact', name='operational_key', field=models.UUIDField(blank=True, default=None, null=True, verbose_name='Идентификатор оперативной работы')),
        migrations.AddField(model_name='dailyfact', name='operational_name', field=models.CharField(blank=True, default='', max_length=255, verbose_name='Название прочей работы')),
        migrations.AddField(model_name='dailyfact', name='operational_unit', field=models.CharField(blank=True, default='', max_length=50, verbose_name='Единица измерения прочей работы')),
        migrations.AddField(model_name='employeeworklink', name='fact', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='employee_links', to='production.dailyfact')),
        migrations.RemoveConstraint(model_name='employeeworklink', name='unique_employee_simple_work'),
        migrations.AddConstraint(model_name='employeeworklink', constraint=models.UniqueConstraint(condition=models.Q(('fact__isnull', True), ('work_item__isnull', True)), fields=('activity', 'work'), name='unique_employee_simple_work')),
        migrations.RemoveConstraint(model_name='dailyfact', name='unique_simple_fact'),
        migrations.AddConstraint(model_name='dailyfact', constraint=models.UniqueConstraint(condition=models.Q(('operational_key__isnull', True), ('work_item__isnull', True)), fields=('project_work', 'date'), name='unique_simple_fact')),
        migrations.AddConstraint(model_name='dailyfact', constraint=models.UniqueConstraint(condition=models.Q(('operational_key__isnull', False)), fields=('date', 'operational_key'), name='unique_operational_fact')),
        migrations.AddConstraint(model_name='employeeworklink', constraint=models.UniqueConstraint(condition=models.Q(('fact__isnull', False)), fields=('activity', 'fact'), name='unique_employee_operational_fact')),
    ]
