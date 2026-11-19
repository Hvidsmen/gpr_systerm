import re
from collections import defaultdict
from django.db import migrations


INVALID_NAMES={'нет','не назначен','ответственный hr','фио','nan','none','null','готов'}
INVALID_FRAGMENTS=(
    'отказ','кандидат ','готов после','готов к ','готов с ','готово ','по готовности','не готов',
    'билет','уволь','обстоятель','уточн','узнаю','другую компанию','агентств','штат ',
    'семейн','не выходит на работу','попросил отменить','причину сообщит',
)
DATE_RE=re.compile(r'(?:\d{4}[-./]\d{1,2}[-./]\d{1,2}|\d{1,2}[-./]\d{1,2}[-./]\d{4})(?:[ t]\d{1,2}:\d{2}(?::\d{2})?)?')


def normalized(value):
    return ' '.join(str(value or '').split()).casefold()


def base_position(value):
    value=' '.join(str(value or '').split())
    return re.sub(r'(?:\s*\([^()]*(?:\)|$))+\s*$','',value).strip(' ,;-/').casefold()


def clean_directories(apps,schema_editor):
    Specialist=apps.get_model('personnel','RecruitmentSpecialist')
    Candidate=apps.get_model('personnel','TalentPoolCandidate')
    Position=apps.get_model('resources','Position')
    invalid=[];keepers={}
    for row in Specialist.objects.order_by('company_id','pk').only('pk','company_id','name','user_id','is_active').iterator():
        name=normalized(row.name)
        if (not name or name in INVALID_NAMES or row.name.startswith('#') or DATE_RE.fullmatch(row.name)
                or any(fragment in name for fragment in INVALID_FRAGMENTS)):
            invalid.append(row.pk)
            continue
        key=(row.company_id,name)
        keeper=keepers.get(key)
        if keeper:
            Candidate.objects.filter(responsible_hr_id=row.pk).update(responsible_hr_id=keeper.pk)
            changed=[]
            if not keeper.user_id and row.user_id:keeper.user_id=row.user_id;changed.append('user')
            if row.is_active and not keeper.is_active:keeper.is_active=True;changed.append('is_active')
            if changed:keeper.save(update_fields=changed)
            row.delete()
        else:keepers[key]=row
    if invalid:Specialist.objects.filter(pk__in=invalid).update(is_active=False)

    exact=defaultdict(lambda:defaultdict(list));base=defaultdict(lambda:defaultdict(list))
    for row in Position.objects.only('pk','company_id','name').iterator():
        exact[row.company_id][normalized(row.name)].append(row.pk)
        base[row.company_id][base_position(row.name)].append(row.pk)
    updates=[]
    for row in Candidate.objects.filter(position_id__isnull=True).only('pk','company_id','source_data').iterator():
        source=row.source_data or {};name=normalized(source.get('position'))
        if not name:continue
        ids=exact[row.company_id].get(name,[])
        if len(ids)!=1:
            stripped=base_position(source.get('position'));ids=base[row.company_id].get(stripped,[]) if stripped else []
        if len(ids)==1:row.position_id=ids[0];updates.append(row)
    if updates:Candidate.objects.bulk_update(updates,['position'],batch_size=500)


class Migration(migrations.Migration):
    dependencies=[('personnel','0052_talentpoolcandidate_archive')]
    operations=[migrations.RunPython(clean_directories,migrations.RunPython.noop)]
