from django.db import migrations


def seed(apps, schema_editor):
    roles = apps.get_model('accounts', 'Role').objects.using(schema_editor.connection.alias)
    for code, name, description in [
        ('PERSONNEL_CATALOG_MANAGER', 'Ответственный за справочники сотрудников и должностей',
         'Создание и изменение сотрудников, должностей и групп должностей своей компании.'),
        ('EQUIPMENT_CATALOG_MANAGER', 'Ответственный за справочник техники',
         'Создание и изменение плановой техники, моделей, машин, групп техники и экипажей своей компании.'),
    ]:
        roles.get_or_create(code=code, defaults={'name': name, 'description': description})


class Migration(migrations.Migration):
    dependencies = [('accounts', '0020_catalogue_schedule_times')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
