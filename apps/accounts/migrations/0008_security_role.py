from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('accounts','Role').objects.using(schema_editor.connection.alias).get_or_create(code='SECURITY_HEAD',defaults={'name':'Служба безопасности'})


class Migration(migrations.Migration):
    dependencies=[('accounts','0007_dispatcher_role')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
