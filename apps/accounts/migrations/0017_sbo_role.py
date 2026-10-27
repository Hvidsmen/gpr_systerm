from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('accounts', 'Role').objects.using(schema_editor.connection.alias).get_or_create(
        code='SBO_HEAD', defaults={'name': 'Служба СБО'},
    )


class Migration(migrations.Migration):
    dependencies = [('accounts', '0016_positions_import_schedule')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
