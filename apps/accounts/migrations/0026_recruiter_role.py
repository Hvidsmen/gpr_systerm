from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('accounts','Role').objects.using(schema_editor.connection.alias).get_or_create(
        code='RECRUITER', defaults={'name':'Подбор персонала','description':'Заявки диспетчеров и подбор новых кандидатов.'})


class Migration(migrations.Migration):
    dependencies = [('accounts','0025_trainingticketuse_trainingcopy_trainingresetrun_and_more')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
