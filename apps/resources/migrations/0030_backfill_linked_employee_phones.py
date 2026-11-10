from django.db import migrations


def backfill_phones(apps,schema_editor):
    Employee=apps.get_model('resources','Employee')
    Person=apps.get_model('personnel','Person')
    database=schema_editor.connection.alias
    for employee_id,phone in Person.objects.using(database).exclude(employee_id=None).exclude(phone='').values_list('employee_id','phone').iterator():
        Employee.objects.using(database).filter(pk=employee_id,phone='').update(phone=phone)


class Migration(migrations.Migration):
    dependencies=[('resources','0029_employee_phone'),('personnel','0035_movement_approval_route')]
    operations=[migrations.RunPython(backfill_phones,migrations.RunPython.noop)]
