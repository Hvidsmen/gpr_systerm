from django.db import migrations


def unify(apps, schema_editor):
    db = schema_editor.connection.alias
    Position = apps.get_model('resources', 'Position')
    Brigade = apps.get_model('resources', 'Brigade')
    Mapping = apps.get_model('resources', 'PositionPlanningBrigade')
    for position in Position.objects.using(db).exclude(pk__in=Mapping.objects.using(db).values('position_id')).iterator():
        used = Mapping.objects.using(db).values('brigade_id')
        brigade = Brigade.objects.using(db).filter(company_id=position.company_id, name__iexact=position.name).exclude(pk__in=used).order_by('pk').first()
        if brigade is None:
            brigade = Brigade.objects.using(db).create(company_id=position.company_id, code=f'POS-{position.pk}', name=position.name)
        Mapping.objects.using(db).create(company_id=position.company_id, position_id=position.pk, brigade_id=brigade.pk)
    for brigade in Brigade.objects.using(db).exclude(pk__in=Mapping.objects.using(db).values('brigade_id')).iterator():
        position = Position.objects.using(db).create(company_id=brigade.company_id, code=f'CAT-{brigade.pk}', name=brigade.name[:100])
        Mapping.objects.using(db).create(company_id=brigade.company_id, position_id=position.pk, brigade_id=brigade.pk)
    for mapping in Mapping.objects.using(db).select_related('brigade').iterator():
        Position.objects.using(db).filter(pk=mapping.position_id, company_id=mapping.company_id).update(name=mapping.brigade.name[:100])


class Migration(migrations.Migration):
    dependencies = [('resources', '0011_equipment_driver_requirements')]
    operations = [migrations.RunPython(unify, migrations.RunPython.noop)]
