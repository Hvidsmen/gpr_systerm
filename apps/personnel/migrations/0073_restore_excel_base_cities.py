from django.db import migrations

from apps.personnel.russian_cities import normalize_city_name


def source_city(source):
    value=normalize_city_name((source or {}).get('base_city'))
    if value:return value
    for event in (source or {}).get('funnel_sources') or []:
        value=normalize_city_name((event.get('data') or {}).get('base_city'))
        if value:return value
    return ''


def restore_excel_base_cities(apps,schema_editor):
    Candidate=apps.get_model('personnel','TalentPoolCandidate')
    BaseCity=apps.get_model('personnel','BaseCity')
    companies={}
    cities=BaseCity.objects.filter(is_active=True,merged_into__isnull=True).order_by('pk')
    for city in cities.iterator():
        companies.setdefault(city.company_id,{})[normalize_city_name(city.name).casefold()]=city
    rows=Candidate.objects.filter(base_city__isnull=True).only('pk','company_id','source_data')
    for candidate in rows.iterator(chunk_size=500):
        name=source_city(candidate.source_data)
        city=companies.get(candidate.company_id,{}).get(name.casefold()) if name else None
        if not city:continue
        source=dict(candidate.source_data or {})
        source['base_city']=city.name
        Candidate.objects.filter(pk=candidate.pk,base_city__isnull=True).update(base_city_id=city.pk,source_data=source)


class Migration(migrations.Migration):
    dependencies=[('personnel','0072_talent_pool_documents')]
    operations=[migrations.RunPython(restore_excel_base_cities,migrations.RunPython.noop)]
