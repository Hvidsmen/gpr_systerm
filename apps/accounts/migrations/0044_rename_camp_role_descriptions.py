from django.db import migrations


NEW_DESCRIPTIONS = {
    'FUEL_WAREHOUSE': (
        'Дневной учёт ГСМ в местонахождениях, связанных с назначенными объектами: '
        'приход, расход и остаток.'
    ),
    'COMMANDANT': (
        'Работа в разделе «Комендант»: проживание, заселение, переселение, '
        'выселение и места размещения.'
    ),
}

OLD_DESCRIPTIONS = {
    'FUEL_WAREHOUSE': (
        'Дневной учёт ГСМ на назначенных объектах и связанных с ними ВЖГ: '
        'приход, расход и остаток.'
    ),
    'COMMANDANT': (
        'Работа в разделе «Комендант»: проживание, заселение, переселение, '
        'выселение и места ВЖГ.'
    ),
}


def update_descriptions(apps, schema_editor, descriptions):
    role_model = apps.get_model('accounts', 'Role')
    database = schema_editor.connection.alias
    for code, description in descriptions.items():
        role_model.objects.using(database).filter(code=code).update(description=description)


def forwards(apps, schema_editor):
    update_descriptions(apps, schema_editor, NEW_DESCRIPTIONS)


def backwards(apps, schema_editor):
    update_descriptions(apps, schema_editor, OLD_DESCRIPTIONS)


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0043_recruitment_roles'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
