from django.db import migrations


def fix_open_requests(apps,schema_editor):
    Request=apps.get_model('personnel','MovementReviewRequest')
    rows=[]
    for row in Request.objects.filter(status='OPEN',source='DISPATCHER'):
        changed=False
        if not row.planning_position_name and row.position_name:
            row.planning_position_name=row.position_name;changed=True
        if row.travel_status in {'Прибыл','Убыл'}:
            row.travel_status='Ожидается';changed=True
        if changed:rows.append(row)
    if rows:Request.objects.bulk_update(rows,['planning_position_name','travel_status'])


class Migration(migrations.Migration):
    dependencies=[('personnel','0026_movement_review_request_source')]
    operations=[migrations.RunPython(fix_open_requests,migrations.RunPython.noop)]
