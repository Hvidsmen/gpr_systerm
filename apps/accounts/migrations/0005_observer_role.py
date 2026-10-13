from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('accounts', 'Role').objects.using(schema_editor.connection.alias).get_or_create(
        code='OBSERVER', defaults={'name': 'Наблюдатель'})


class Migration(migrations.Migration):
    dependencies = [('accounts', '0004_approval_roles')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
