from django.db import migrations, models


STAGES = [
    ('NEW', 'Новый'),
    ('SCREENING', 'На рассмотрении'),
    ('HR_INTERVIEW', 'Собеседование HR'),
    ('MANAGER_APPROVAL', 'Направлен на согласование Заказчику'),
    ('SECURITY_CHECK', 'На СБ'),
    ('MEDICAL_DOCUMENTS', 'На МК'),
    ('PASS_PROCESSING', 'На пропуске'),
    ('SELECTED', 'Отобран'),
    ('READY', 'Готов к заезду'),
    ('TRANSFERRED', 'Поставлен на заезд'),
    ('ARRIVAL_APPROVED', 'Заезд согласован'),
    ('ARRIVED', 'Заехал'),
    ('EMPLOYED', 'Трудоустроен'),
    ('DISMISSED', 'Уволен'),
    ('RESERVE', 'Резерв'),
    ('REJECTED', 'Отказ работодателя'),
    ('WITHDRAWN', 'Отказ кандидата'),
    ('NOT_REQUIRED', 'Не потребовался'),
    ('NO_SHOW', 'Не заехал'),
    ('BLACKLIST', 'Черный список'),
]


def mark_dismissed(apps, schema_editor):
    Candidate = apps.get_model('personnel', 'TalentPoolCandidate')
    Event = apps.get_model('personnel', 'TalentPoolStageEvent')
    rows = Candidate.objects.filter(employed_employee__is_active=False).exclude(stage='DISMISSED')
    for row in rows.iterator(chunk_size=200):
        previous = row.stage
        Candidate.objects.filter(pk=row.pk).update(stage='DISMISSED', is_active=False)
        Event.objects.create(company_id=row.company_id, candidate_id=row.pk, from_stage=previous,
            to_stage='DISMISSED', actor_id=row.created_by_id,
            note='Автоматически отмечен уволенным по текущему состоянию сотрудника ЗУП.')


class Migration(migrations.Migration):

    dependencies = [('personnel', '0069_seed_russian_base_cities')]

    operations = [
        migrations.AlterField(
            model_name='recruitmentcandidate',
            name='status',
            field=models.CharField(choices=STAGES, default='NEW', max_length=24, verbose_name='Статус'),
        ),
        migrations.AlterField(
            model_name='talentpoolcandidate',
            name='stage',
            field=models.CharField(choices=STAGES, default='NEW', max_length=24, verbose_name='Этап воронки'),
        ),
        migrations.AlterField(
            model_name='talentpoolstageevent',
            name='from_stage',
            field=models.CharField(choices=STAGES, max_length=24, verbose_name='Предыдущий этап'),
        ),
        migrations.AlterField(
            model_name='talentpoolstageevent',
            name='to_stage',
            field=models.CharField(choices=STAGES, max_length=24, verbose_name='Новый этап'),
        ),
        migrations.RunPython(mark_dismissed, migrations.RunPython.noop),
    ]
