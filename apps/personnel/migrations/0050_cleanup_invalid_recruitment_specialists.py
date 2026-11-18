import re
from django.db import migrations


INVALID_NAMES={'нет','не назначен','ответственный hr','фио','nan','none','null'}
DATE_RE=re.compile(
    r'(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4})(?:[ t]\d{1,2}:\d{2}(?::\d{2})?)?'
)


def normalized(value):
    name=' '.join(str(value or '').split())
    folded=name.casefold()
    if (not name or folded in INVALID_NAMES or name.startswith('#') or DATE_RE.fullmatch(name)
            or not any(character.isalpha() for character in name)):
        return ''
    return name


def clean_source_data(source):
    source=dict(source or {})
    changed=False
    if source.get('responsible_hr') and not normalized(source['responsible_hr']):
        source['responsible_hr']='';changed=True
    events=[]
    for event in source.get('funnel_sources',[]):
        event=dict(event or {});data=dict(event.get('data') or {})
        if data.get('responsible_hr') and not normalized(data['responsible_hr']):
            data['responsible_hr']='';event['data']=data;changed=True
        events.append(event)
    if events and changed:source['funnel_sources']=events
    return source,changed


def clean_invalid_specialists(apps,schema_editor):
    Candidate=apps.get_model('personnel','TalentPoolCandidate')
    Specialist=apps.get_model('personnel','RecruitmentSpecialist')
    invalid_ids=[row.pk for row in Specialist.objects.all().iterator(chunk_size=500) if not normalized(row.name)]
    if invalid_ids:
        Candidate.objects.filter(responsible_hr_id__in=invalid_ids).update(responsible_hr_id=None)
        Specialist.objects.filter(pk__in=invalid_ids).delete()
    for candidate in Candidate.objects.all().iterator(chunk_size=500):
        source,changed=clean_source_data(candidate.source_data)
        if changed:Candidate.objects.filter(pk=candidate.pk).update(source_data=source)


class Migration(migrations.Migration):
    dependencies=[('personnel','0049_recruitment_request_position')]
    operations=[migrations.RunPython(clean_invalid_specialists,migrations.RunPython.noop)]
