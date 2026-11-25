from django.db import migrations

from apps.personnel.russian_cities import RUSSIAN_CITIES, normalize_city_name


def normalized_key(value):
    return normalize_city_name(value).casefold()


def rewrite_candidate_sources(Candidate, company_id, old_name, new_name):
    expected = ' '.join(str(old_name or '').split()).casefold()
    for candidate in Candidate.objects.filter(company_id=company_id).iterator():
        source = dict(candidate.source_data or {})
        current = ' '.join(str(source.get('base_city') or '').split()).casefold()
        if current != expected:
            continue
        source['base_city'] = new_name
        candidate.source_data = source
        candidate.save(update_fields=['source_data'])


def normalize_existing_prefixes(apps, company_id):
    BaseCity = apps.get_model('personnel', 'BaseCity')
    Candidate = apps.get_model('personnel', 'TalentPoolCandidate')
    Movement = apps.get_model('personnel', 'MovementReviewRequest')
    cities = list(BaseCity.objects.filter(company_id=company_id).order_by('pk'))
    for city in cities:
        canonical = normalize_city_name(city.name)
        if canonical == ' '.join(city.name.split()) or not city.is_active or city.merged_into_id:
            continue
        target = next((row for row in cities if row.pk != city.pk and normalized_key(row.name) == canonical.casefold() and not row.merged_into_id), None)
        if target is None:
            old_name = city.name
            city.name = canonical
            city.save(update_fields=['name'])
            Movement.objects.filter(company_id=company_id, base_city=old_name).update(base_city=canonical)
            rewrite_candidate_sources(Candidate, company_id, old_name, canonical)
            continue
        old_name = city.name
        if not target.is_active:
            target.is_active = True
            target.archived_at = None
            target.archived_by_id = None
            target.status_reason = ''
            target.save(update_fields=['is_active', 'archived_at', 'archived_by', 'status_reason'])
        Candidate.objects.filter(company_id=company_id, base_city_id=city.pk).update(base_city_id=target.pk)
        Movement.objects.filter(company_id=company_id, base_city=old_name).update(base_city=target.name)
        rewrite_candidate_sources(Candidate, company_id, old_name, target.name)
        BaseCity.objects.filter(company_id=company_id, merged_into_id=city.pk).update(merged_into_id=target.pk)
        city.delete()


def seed_cities(apps, schema_editor):
    Company = apps.get_model('accounts', 'Company')
    BaseCity = apps.get_model('personnel', 'BaseCity')
    for company_id in Company.objects.values_list('pk', flat=True).iterator():
        normalize_existing_prefixes(apps, company_id)
        existing = {normalized_key(name) for name in BaseCity.objects.filter(company_id=company_id).values_list('name', flat=True)}
        missing = [BaseCity(company_id=company_id, name=name) for name in RUSSIAN_CITIES if normalized_key(name) not in existing]
        # The missing-name set is computed above.  Do not use
        # ignore_conflicts here: Django's MSSQL backend does not support it.
        BaseCity.objects.bulk_create(missing, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0068_rename_staffing_assignment_labels'),
    ]

    operations = [
        migrations.RunPython(seed_cities, migrations.RunPython.noop),
    ]
