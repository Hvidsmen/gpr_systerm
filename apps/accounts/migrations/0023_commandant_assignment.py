from django.db import migrations


def expose(apps,schema_editor):
    Role=apps.get_model('accounts','Role')
    Role.objects.using(schema_editor.connection.alias).update_or_create(code='COMMANDANT',defaults={'name':'Комендант участка','description':'Работа в разделе «Комендант»: проживание, заселение, переселение, выселение и места ВЖГ.'})


class Migration(migrations.Migration):
    dependencies=[('accounts','0022_fuel_warehouse_role')]
    operations=[migrations.RunPython(expose,migrations.RunPython.noop)]
