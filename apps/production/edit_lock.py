from datetime import timedelta
from uuid import UUID
from django.core.exceptions import ValidationError
from django.utils import timezone
from .dispatch_models import ProductionEditLock
from .work_claims import owner_name

TTL=timedelta(minutes=5)


def session_value(data):
    raw=data.get('session') or data.get('claim_session')
    try:return UUID(str(raw))
    except (TypeError,ValueError,AttributeError):return None


def acquire(user,obj,day,session):
    now=timezone.now();ProductionEditLock.objects.filter(company=obj.company,construction_object=obj,date=day,expires_at__lte=now).delete()
    lock=ProductionEditLock.objects.select_for_update().filter(company=obj.company,construction_object=obj,date=day).select_related('user').first()
    if lock is None:
        lock=ProductionEditLock.objects.create(company=obj.company,construction_object=obj,date=day,user=user,session_key=session,expires_at=now+TTL)
    mine=lock.user_id==user.pk and lock.session_key==session
    if mine:
        lock.expires_at=now+TTL;lock.save(update_fields=['expires_at','updated_at'])
    return {'mine':mine,'owner':owner_name(lock.user),'expires_at':lock.expires_at.isoformat()}


def release(user,obj,day,session):
    if session:ProductionEditLock.objects.filter(company=obj.company,construction_object=obj,date=day,user=user,session_key=session).delete()


def require_available(user,obj,day,data):
    now=timezone.now();ProductionEditLock.objects.filter(company=obj.company,construction_object=obj,date=day,expires_at__lte=now).delete()
    lock=ProductionEditLock.objects.filter(company=obj.company,construction_object=obj,date=day).select_related('user').first()
    if lock is None:return
    session=session_value(data)
    if lock.user_id!=user.pk or lock.session_key!=session:
        raise ValidationError(f'Факт производства сейчас редактирует {owner_name(lock.user)}. Доступен только просмотр.')
