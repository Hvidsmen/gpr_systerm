from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('accounts', 'Role').objects.using(schema_editor.connection.alias).get_or_create(
        code='FUEL_WAREHOUSE', defaults={'name': 'Склад ГСМ', 'description': 'Дневной учёт ГСМ на назначенных объектах и связанных с ними ВЖГ: приход, расход и остаток.'})


class Migration(migrations.Migration):
    dependencies = [('accounts', '0021_catalogue_manager_roles')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
