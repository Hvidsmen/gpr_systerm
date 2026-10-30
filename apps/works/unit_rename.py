"""Confirmed 1:1 spelling changes update live references, not quantities/history."""
import hashlib,json
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import PLAN_ROLES,require_roles
from apps.accounts.models import Company
from .models import MeasurementUnit,MeasurementUnitAlias
from .unit_merge import MODELS
from .catalogs import UnitForm

SALT='measurement-unit-rename-v1'


def state(unit):
    refs={m._meta.label:list(m.objects.filter(company=unit.company,unit=unit.symbol).order_by('pk').values_list('pk','unit')) for m in MODELS}
    aliases=list(MeasurementUnitAlias.objects.filter(company=unit.company).order_by('pk').values_list('symbol','target_id'))
    digest=hashlib.sha256(json.dumps([unit.pk,unit.symbol,unit.name,str(unit.updated_at),refs,aliases],sort_keys=True).encode()).hexdigest()
    return digest,[{'label':str(m._meta.verbose_name_plural),'count':len(refs[m._meta.label])} for m in MODELS]


def preview(user,unit,values):
    digest,summary=state(unit)
    data={'user':user.pk,'company':user.company_id,'unit':unit.pk,'before':unit.symbol,'symbol':values['symbol'],'name':values['name'],'digest':digest}
    return {'token':signing.dumps(data,salt=SALT,compress=True),'summary':summary,'before':unit.symbol,'symbol':values['symbol'],'name':values['name']}


@transaction.atomic
def apply(user,pk,token,equivalent):
    require_roles(user,PLAN_ROLES)
    if not equivalent:raise ValidationError('Подтвердите, что меняется только обозначение, без пересчёта объёмов.')
    try:data=signing.loads(token,salt=SALT,max_age=1800)
    except signing.BadSignature:raise ValidationError('Предпросмотр устарел. Повторите изменение обозначения.')
    if (data.get('user'),data.get('company'),data.get('unit'))!=(user.pk,user.company_id,pk):raise ValidationError('Предпросмотр относится к другой единице или пользователю.')
    from django.db.models import F
    Company.objects.filter(pk=user.company_id).update(name=F('name'))
    unit=MeasurementUnit.objects.select_for_update().filter(company=user.company,pk=pk).first()
    if unit is None:raise ValidationError('Единица удалена. Обновите справочник.')
    digest,summary=state(unit)
    if digest!=data['digest']:raise ValidationError('Единица или её использование изменились. Повторите предпросмотр.')
    form=UnitForm({'symbol':data['symbol'],'name':data['name']},instance=unit,company=user.company)
    if not form.is_valid():raise ValidationError([str(e) for errors in form.errors.values() for e in errors])
    for model in MODELS:
        if len(data['symbol'])>model._meta.get_field('unit').max_length and model.objects.filter(company=user.company,unit=data['before']).exists():raise ValidationError('Новое обозначение слишком длинное для связанных записей.')
    for model in MODELS:model.objects.filter(company=user.company,unit=data['before']).update(unit=data['symbol'])
    unit=form.save()
    MeasurementUnitAlias.objects.filter(company=user.company,symbol=unit.symbol,target=unit).delete()
    MeasurementUnitAlias.objects.update_or_create(company=user.company,symbol=data['before'],defaults={'target':unit})
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='works.MeasurementUnit',entity_id=str(pk),old_value={'symbol':data['before']},new_value={'symbol':unit.symbol,'name':unit.name,'references':summary},comment='Подтверждено изменение обозначения единицы 1:1')
    return unit
