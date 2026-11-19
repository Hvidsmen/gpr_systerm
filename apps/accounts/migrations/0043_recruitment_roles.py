from django.db import migrations


def create_recruitment_roles(apps,schema_editor):
    Role=apps.get_model('accounts','Role')
    database=schema_editor.connection.alias
    Role.objects.using(database).update_or_create(code='RECRUITER',defaults={
        'name':'Рекрутер',
        'description':'Ведение назначенных заявок на подбор и карточек кандидатов.',
    })
    Role.objects.using(database).update_or_create(code='RECRUITMENT_HEAD',defaults={
        'name':'Руководитель банка сотрудников',
        'description':'Управление банком сотрудников, назначение ответственных рекрутеров и контроль заявок на подбор.',
    })


class Migration(migrations.Migration):
    dependencies=[('accounts','0042_training_invitation_batches')]
    operations=[migrations.RunPython(create_recruitment_roles,migrations.RunPython.noop)]
