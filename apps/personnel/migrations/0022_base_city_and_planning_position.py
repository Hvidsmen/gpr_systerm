import django.db.models.deletion
from django.db import migrations, models


def seed_request_details(apps, schema_editor):
    BaseCity = apps.get_model('personnel', 'BaseCity')
    Request = apps.get_model('personnel', 'MovementReviewRequest')
    Slot = apps.get_model('personnel', 'StaffingSlot')
    for row in Request.objects.select_related('person').all().iterator():
        city = ' '.join((row.base_city or '').split())
        if city:
            exists = BaseCity.objects.filter(company_id=row.company_id, name__iexact=city).exists()
            if not exists:
                BaseCity.objects.create(company_id=row.company_id, name=city)
        if row.planning_position_name:
            continue
        slot = Slot.objects.filter(
            person_id=row.person_id,
            cancelled_at__isnull=True,
            assigned_from__lte=row.movement_date,
        ).filter(
            models.Q(assigned_until__isnull=True) | models.Q(assigned_until__gte=row.movement_date),
            models.Q(replaced_on__isnull=True) | models.Q(replaced_on__gt=row.movement_date),
        ).select_related('planning_position', 'group').order_by('-assigned_from', '-pk').first()
        if slot:
            row.planning_position_name = str(slot.planning_position or slot.group.position or '')
            row.save(update_fields=['planning_position_name'])


class Migration(migrations.Migration):
    dependencies = [('personnel', '0021_review_request_before_fact')]
    operations = [
        migrations.CreateModel(
            name='BaseCity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('name', models.CharField(max_length=150, verbose_name='Базовый город')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='accounts.company', verbose_name='company')),
            ],
            options={'ordering': ['name', 'pk']},
        ),
        migrations.AddField(
            model_name='movementreviewrequest',
            name='planning_position_name',
            field=models.CharField(blank=True, max_length=250, verbose_name='Должность по ГПР / СМГ'),
        ),
        migrations.AlterField(
            model_name='movementreviewrequest',
            name='position_name',
            field=models.CharField(blank=True, max_length=250, verbose_name='Должность по справочнику сотрудников'),
        ),
        migrations.AddConstraint(
            model_name='basecity',
            constraint=models.UniqueConstraint(fields=('company', 'name'), name='personnel_unique_base_city'),
        ),
        migrations.RunPython(seed_request_details, migrations.RunPython.noop),
    ]
