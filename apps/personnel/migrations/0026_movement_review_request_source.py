from django.db import migrations,models
def classify_existing(apps,schema_editor):
    Request=apps.get_model('personnel','MovementReviewRequest')
    Request.objects.filter(requested_by__isnull=False).exclude(requested_by__role__code='PERSONNEL_DISPATCHER').update(source='EXCEL')


class Migration(migrations.Migration):
    dependencies=[('personnel','0025_zup_schedule_change_workflow')]
    operations=[
        migrations.AddField(model_name='movementreviewrequest',name='source',field=models.CharField(choices=[('DISPATCHER','Портал'),('EXCEL','Excel')],default='DISPATCHER',max_length=12,verbose_name='Источник заявки')),
        migrations.RunPython(classify_existing,migrations.RunPython.noop),
    ]
