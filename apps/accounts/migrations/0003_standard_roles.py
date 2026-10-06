from django.db import migrations


def create_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    for code, name in [('ADMIN', 'Администратор'), ('PLANNER', 'Планировщик'), ('MANAGER', 'Руководитель'), ('FOREMAN', 'Мастер')]:
        Role.objects.using(schema_editor.connection.alias).get_or_create(code=code, defaults={'name': name})


class Migration(migrations.Migration):
    dependencies = [('accounts', '0002_user_assigned_objects')]
    operations = [migrations.RunPython(create_roles, migrations.RunPython.noop)]
