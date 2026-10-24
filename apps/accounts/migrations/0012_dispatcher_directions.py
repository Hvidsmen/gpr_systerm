from django.db import migrations

def seed(apps,schema_editor):
    Role=apps.get_model('accounts','Role')
    db=schema_editor.connection.alias
    Role.objects.using(db).filter(code='DISPATCHER').update(name='Диспетчер по производству')
    for code,name in [('PERSONNEL_DISPATCHER','Диспетчер по персоналу'),('MECHANIZATION_DISPATCHER','Диспетчер по механизации')]:
        Role.objects.using(db).get_or_create(code=code,defaults={'name':name})
class Migration(migrations.Migration):
    dependencies=[('accounts','0011_one_c_diagnostics')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
