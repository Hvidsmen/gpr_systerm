from django.db import migrations


def seed(apps,schema_editor):
    apps.get_model('accounts','Role').objects.using(schema_editor.connection.alias).get_or_create(
        code='TRANSPORT_DISPATCHER',defaults={'name':'Диспетчер по транспорту','description':'Рекомендации и фактические заезды и выезды сотрудников.'})


class Migration(migrations.Migration):
    dependencies=[('accounts','0028_production_additional_objects')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
