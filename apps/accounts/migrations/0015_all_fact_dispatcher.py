from django.db import migrations


def seed(apps,schema_editor):
    apps.get_model('accounts','Role').objects.using(schema_editor.connection.alias).get_or_create(
        code='ALL_FACT_DISPATCHER',defaults={'name':'Диспетчер (все факты)'})


class Migration(migrations.Migration):
    dependencies=[('accounts','0014_one_c_metadata_names')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
