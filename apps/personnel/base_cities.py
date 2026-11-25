"""Safe catalogue operations for base cities and their denormalized references."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import BaseCity,MovementReviewRequest,TalentPoolCandidate
from .russian_cities import normalize_city_name


def normalize_name(value):
    return normalize_city_name(value)


def usage_map(company):
    cities=list(BaseCity.objects.filter(company=company).values('pk','name','merged_into_id'))
    result={row['pk']:{'candidates':0,'source_only':0,'movements':0,'merge_targets':0,'total':0} for row in cities}
    by_name={normalize_name(row['name']).casefold():row['pk'] for row in cities}
    for row in cities:
        if row['merged_into_id'] in result:result[row['merged_into_id']]['merge_targets']+=1
    for city_id,source in TalentPoolCandidate.objects.filter(company=company).values_list('base_city_id','source_data'):
        if city_id in result:result[city_id]['candidates']+=1
        source_id=by_name.get(normalize_name((source or {}).get('base_city')).casefold())
        if source_id in result and source_id!=city_id:result[source_id]['source_only']+=1
    for name in MovementReviewRequest.objects.filter(company=company).values_list('base_city',flat=True):
        city_id=by_name.get(normalize_name(name).casefold())
        if city_id in result:result[city_id]['movements']+=1
    for counts in result.values():counts['total']=sum(counts.values())
    return result


def usage(city):
    return usage_map(city.company).get(city.pk,{'candidates':0,'source_only':0,'movements':0,'merge_targets':0,'total':0})


def _rewrite_candidate_sources(company,old_name,new_name):
    changed=0
    for row in TalentPoolCandidate.objects.filter(company=company).iterator():
        source=dict(row.source_data or {})
        if normalize_name(source.get('base_city')).casefold()!=normalize_name(old_name).casefold():continue
        source['base_city']=new_name;row.source_data=source
        row.save(update_fields=['source_data','updated_at']);changed+=1
    return changed


def _movement_ids(company,name):
    expected=normalize_name(name).casefold()
    return [pk for pk,value in MovementReviewRequest.objects.filter(company=company).values_list('pk','base_city')
        if normalize_name(value).casefold()==expected]


def _duplicate(company,name,exclude=None):
    expected=normalize_name(name).casefold()
    return any(normalize_name(value).casefold()==expected for value in
        BaseCity.objects.filter(company=company).exclude(pk=exclude).values_list('name',flat=True))


def find(company,name,active=None):
    expected=normalize_name(name).casefold();query=BaseCity.objects.filter(company=company)
    if active is not None:query=query.filter(is_active=active)
    return next((row for row in query if normalize_name(row.name).casefold()==expected),None)


@transaction.atomic
def create(*,company,name):
    name=normalize_name(name)
    if not name:raise ValidationError('Укажите название города.')
    if _duplicate(company,name):
        raise ValidationError('Город с таким названием уже существует, в том числе в архиве.')
    return BaseCity.objects.create(company=company,name=name)


@transaction.atomic
def rename(*,company,city_id,name):
    city=BaseCity.objects.select_for_update().get(company=company,pk=city_id)
    name=normalize_name(name);old_name=city.name
    if not name:raise ValidationError('Укажите название города.')
    if _duplicate(company,name,city.pk):
        raise ValidationError('Город с таким названием уже существует.')
    if old_name==name:return city
    city.name=name;city.full_clean();city.save(update_fields=['name','updated_at'])
    MovementReviewRequest.objects.filter(pk__in=_movement_ids(company,old_name)).update(base_city=name)
    _rewrite_candidate_sources(company,old_name,name)
    return city


@transaction.atomic
def archive(*,company,city_id,actor,reason=''):
    city=BaseCity.objects.select_for_update().get(company=company,pk=city_id)
    if city.merged_into_id:raise ValidationError('Этот город уже объединён с другим.')
    city.is_active=False;city.archived_at=timezone.now();city.archived_by=actor
    city.status_reason=normalize_name(reason)
    city.save(update_fields=['is_active','archived_at','archived_by','status_reason','updated_at'])
    return city


@transaction.atomic
def restore(*,company,city_id):
    city=BaseCity.objects.select_for_update().get(company=company,pk=city_id)
    if city.merged_into_id:raise ValidationError('Объединённый город нельзя восстановить. Используйте основной город.')
    city.is_active=True;city.archived_at=None;city.archived_by=None;city.status_reason=''
    city.save(update_fields=['is_active','archived_at','archived_by','status_reason','updated_at'])
    return city


@transaction.atomic
def merge(*,company,source_id,target_id,actor,reason):
    rows={row.pk:row for row in BaseCity.objects.select_for_update().filter(company=company,pk__in=[source_id,target_id])}
    source=rows.get(int(source_id));target=rows.get(int(target_id))
    if not source or not target:raise ValidationError('Выберите два города своей компании.')
    if source.pk==target.pk:raise ValidationError('Нельзя объединить город с самим собой.')
    if source.merged_into_id:raise ValidationError('Этот город уже объединён с другим.')
    if not target.is_active or target.merged_into_id:raise ValidationError('Основным может быть только действующий город.')
    reason=normalize_name(reason)
    if not reason:raise ValidationError('Укажите причину объединения.')
    TalentPoolCandidate.objects.filter(company=company,base_city=source).update(base_city=target)
    MovementReviewRequest.objects.filter(pk__in=_movement_ids(company,source.name)).update(base_city=target.name)
    _rewrite_candidate_sources(company,source.name,target.name)
    source.is_active=False;source.archived_at=timezone.now();source.archived_by=actor
    source.merged_into=target;source.status_reason=reason
    source.save(update_fields=['is_active','archived_at','archived_by','merged_into','status_reason','updated_at'])
    return source,target


@transaction.atomic
def delete(*,company,city_id):
    city=BaseCity.objects.select_for_update().get(company=company,pk=city_id)
    if city.is_active:raise ValidationError('Сначала переместите город в архив.')
    counts=usage(city)
    if counts['total']:
        raise ValidationError('Город используется. Сначала объедините его с другим городом.')
    city.delete()
