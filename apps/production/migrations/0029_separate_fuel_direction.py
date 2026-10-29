from django.db import migrations, models


def preserve_confirmations(apps, schema_editor):
    days = apps.get_model('production', 'FactSectionDay').objects.using(schema_editor.connection.alias)
    # The old mechanization confirmation included fuel. Preserve historical state.
    for row in days.filter(section='mechanization').iterator(chunk_size=500):
        days.get_or_create(company_id=row.company_id, construction_object_id=row.construction_object_id, date=row.date, section='fuel', defaults={'revision': row.revision, 'confirmed_at': row.confirmed_at, 'confirmed_by_id': row.confirmed_by_id, 'deviation_comment': row.deviation_comment})


class Migration(migrations.Migration):
    dependencies = [('production', '0028_work_opening_balance'), ('accounts', '0022_fuel_warehouse_role')]
    operations = [migrations.AlterField(model_name='factsectionday', name='section', field=models.CharField(max_length=16, choices=[('works','Работы'),('labor','Персонал'),('mechanization','Механизация'),('fuel','ГСМ')])), migrations.RunPython(preserve_confirmations, migrations.RunPython.noop)]
