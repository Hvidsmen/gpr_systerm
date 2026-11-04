from django.db import migrations,models


def copy_linked_phones(apps,schema_editor):
    Employee=apps.get_model('resources','Employee')
    Person=apps.get_model('personnel','Person')
    for employee_id,phone in Person.objects.exclude(employee_id=None).exclude(phone='').values_list('employee_id','phone').iterator():
        Employee.objects.filter(pk=employee_id,phone='').update(phone=phone)


class Migration(migrations.Migration):
    dependencies=[('resources','0028_transport_import_source'),('personnel','0024_person_dispatcher_removal')]
    operations=[
        migrations.AddField(model_name='employee',name='phone',field=models.CharField(blank=True,max_length=80,verbose_name='Телефон')),
        migrations.RunPython(copy_linked_phones,migrations.RunPython.noop),
    ]
