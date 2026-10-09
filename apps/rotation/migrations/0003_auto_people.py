from datetime import timedelta
from django.db import migrations


def backfill(apps, schema_editor):
    Role = apps.get_model('rotation', 'RotationRole')
    Person = apps.get_model('rotation', 'RotationPerson')
    using = schema_editor.connection.alias
    positions = Role.objects.using(using).filter(people__isnull=True).select_related('plan').order_by('pk')
    for position in positions.iterator():
        peak = max((row['count'] for row in position.plan.demand if row['brigade_id'] == position.brigade_id), default=0)
        cycle = position.on_days + position.off_days
        if not position.on_days or not position.off_days:
            continue
        count = (peak * cycle + position.on_days - 1) // position.on_days
        if count > 1000:
            # Same roster limit as the application. Leave larger demands for manual review.
            continue
        people = []
        for index in range(count):
            anchor = position.anchor - timedelta(days=index*cycle//count)
            if anchor > position.plan.start:
                anchor -= timedelta(days=((anchor-position.plan.start).days+cycle-1)//cycle*cycle)
            people.append(Person(company_id=position.company_id, position_id=position.pk,
                name=f'Работник {index+1}', on_days=position.on_days, off_days=position.off_days, anchor=anchor))
        Person.objects.using(using).bulk_create(people, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [('rotation', '0002_rotationstatus')]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
