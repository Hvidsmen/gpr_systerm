from django.db import migrations


def backfill_request_numbers(apps, schema_editor):
    Request=apps.get_model('personnel','MovementReviewRequest')
    for row in Request.objects.filter(source='DISPATCHER',request_number='').only('pk'):
        Request.objects.filter(pk=row.pk,request_number='').update(request_number=str(row.pk))


class Migration(migrations.Migration):
    dependencies=[('personnel','0035_movement_approval_route')]
    operations=[migrations.RunPython(backfill_request_numbers,migrations.RunPython.noop)]
