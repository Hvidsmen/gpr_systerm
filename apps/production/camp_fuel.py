"""Optimistic shared editing with atomic, audited balance carry-forward."""
from decimal import Decimal,InvalidOperation
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from core.fact_edit_window import require_fact_day
from core.fact_history import history_actor,with_history_actor
from . import transport, fuel_access
from .camp_fuel_models import CampFuelDay,FUEL_CHOICES

SALT='camp-fuel-v1'


def ledger(camp,fuel_type):return CampFuelDay.objects.filter(company=camp.company,camp=camp,fuel_type=fuel_type).order_by('date','pk')


def _version(camp,fuel_type,rows=None):
    from hashlib import sha256
    import json
    fields=('pk','date','opening_balance','received_liters','actual_liters','actual_balance','comment','updated_at')
    values=list(ledger(camp,fuel_type).values_list(*fields)) if rows is None else [tuple(getattr(row,f) for f in fields) for row in rows]
    return sha256(json.dumps(values,default=str).encode()).hexdigest()


def token(user,camp,day,fuel_type,*,rows=None):
    return signing.dumps({'user':user.pk,'company':user.company_id,'camp':camp.pk,'date':str(day),'fuel':fuel_type,'version':_version(camp,fuel_type,rows)},salt=SALT,compress=True)


def balance_before(camp,day,fuel_type):
    previous=ledger(camp,fuel_type).filter(date__lt=day).last()
    current=ledger(camp,fuel_type).filter(date=day).first()
    return previous,current


def quantity(value):
    try:
        value=Decimal(str(value))
        if not value.is_finite() or value<0 or value>Decimal('999999999999.999') or value!=value.quantize(Decimal('.001')):raise ValueError
        return value
    except (ValueError,InvalidOperation):raise ValidationError('Количество должно быть неотрицательным числом с точностью до 0,001 м³.')


@with_history_actor
@transaction.atomic
def save_day(user,camp,day,fuel_type,*,received,consumed,opening=None,comment='',signed):
    fuel_access.authorize(user,camp)
    require_fact_day(user,day)
    if fuel_type not in dict(FUEL_CHOICES):raise ValidationError('Выберите ГСМ.')
    transport._company_mutex(user)
    from apps.personnel.models import Camp
    camp=Camp.objects.select_for_update().get(pk=camp.pk,company=user.company)
    try:payload=signing.loads(signed,salt=SALT,max_age=12*3600)
    except (signing.BadSignature,TypeError):raise ValidationError('Форма устарела. Обновите страницу.')
    if (payload.get('user'),payload.get('company'),payload.get('camp'),payload.get('date'),payload.get('fuel'))!=(user.pk,user.company_id,camp.pk,str(day),fuel_type):
        raise ValidationError('Форма другого пользователя, ВЖГ, дня или вида ГСМ.')
    rows=list(ledger(camp,fuel_type).select_for_update())
    if payload.get('version')!=_version(camp,fuel_type):raise ValidationError('Баланс уже изменил другой диспетчер. Обновите страницу и проверьте новые значения перед сохранением.')
    if rows and day<rows[0].date:raise ValidationError(f'Учёт этого ГСМ начат {rows[0].date:%d.%m.%Y}. Более раннюю дату нельзя добавить перед начальным остатком.')
    previous=next((r for r in reversed(rows) if r.date<day),None)
    current=next((r for r in rows if r.date==day),None)
    start=previous.actual_balance if previous else quantity(opening if opening is not None else current.opening_balance if current else 0)
    received=quantity(received);consumed=quantity(consumed)
    end=start+received-consumed
    quantity(end)
    if len(comment)>4000:raise ValidationError('Комментарий слишком длинный.')
    current=current or CampFuelDay(company=user.company,camp=camp,date=day,fuel_type=fuel_type,recorded_by=user)
    current.opening_balance=start;current.received_liters=received;current.actual_liters=consumed;current.actual_balance=end;current.comment=comment.strip()
    current.full_clean();current.save()
    with history_actor(user,f'Перенос остатка после исправления ГСМ за {day:%d.%m.%Y}'):
        for row in rows:
            if row.date<=day:continue
            closing=end+row.received_liters-row.actual_liters
            if closing<0:raise ValidationError(f'Изменение даёт отрицательный остаток {row.date:%d.%m.%Y}. Проверьте приход и расход; изменения не сохранены.')
            quantity(closing)
            if row.opening_balance!=end or row.actual_balance!=closing:
                row.opening_balance=end;row.actual_balance=closing;row.full_clean();row.save()
            end=closing
    require_fact_day(user,day)
    return current


def entry_snapshot(user,camp,day,fuel_type):
    rows=list(ledger(camp,fuel_type))
    previous=next((row for row in reversed(rows) if row.date<day),None)
    current=next((row for row in rows if row.date==day),None)
    return previous,current,token(user,camp,day,fuel_type,rows=rows)
