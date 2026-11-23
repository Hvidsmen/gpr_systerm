from django.db import migrations,models


def preserve_commandant_access(apps,schema_editor):
    AccessGroup=apps.get_model('accounts','AccessGroup')
    Camp=apps.get_model('personnel','Camp')
    for group in AccessGroup.objects.filter(role__code='COMMANDANT').iterator(chunk_size=100):
        group.assigned_camps.set(Camp.objects.filter(company_id=group.company_id))


class Migration(migrations.Migration):
    dependencies=[
        ('accounts','0048_uatwaybilltask_allocation_and_more'),
        ('personnel','0066_movement_employment_rehire'),
    ]
    operations=[
        migrations.AddField(
            model_name='accessgroup',name='assigned_camps',
            field=models.ManyToManyField(blank=True,related_name='commandant_access_groups',
                to='personnel.camp',verbose_name='Местонахождения коменданта'),
        ),
        migrations.RunPython(preserve_commandant_access,migrations.RunPython.noop),
    ]
