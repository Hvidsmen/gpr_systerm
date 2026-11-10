from django.db import migrations

ROLES=[
    ('SITE_CHIEF','Начальник участка'),
    ('PEREVAKHTA_SPECIALIST','Специалист перевахты'),
    ('DUDINKA_HEAD','Руководитель обособленного подразделения Дудинка'),
    ('DUDINKA_SECURITY_DEPUTY','Заместитель руководителя ОП по безопасности'),
    ('CHIEF_MECHANIC','Главный механик'),('PARAMEDIC','Фельдшер'),
    ('PERSONNEL_SPECIALIST','Специалист по персоналу'),
    ('LABOR_ECONOMIST','Эксперт по экономике труда и заработной плате'),
]

def create_roles(apps,schema_editor):
    Role=apps.get_model('accounts','Role')
    for code,name in ROLES:Role.objects.using(schema_editor.connection.alias).update_or_create(code=code,defaults={'name':name})

class Migration(migrations.Migration):
    dependencies=[('accounts','0029_transport_dispatcher_role')]
    operations=[migrations.RunPython(create_roles,migrations.RunPython.noop)]
