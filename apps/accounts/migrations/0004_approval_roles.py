from django.db import migrations


def seed(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    for code, name in [('PRODUCTION_HEAD', 'Руководитель производства'), ('HR_HEAD', 'Руководитель кадровой службы'), ('TECH_HEAD', 'Руководитель технической службы'), ('CEO', 'Генеральный директор')]:
        Role.objects.using(schema_editor.connection.alias).get_or_create(code=code, defaults={'name': name})


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_standard_roles')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
