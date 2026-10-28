"""Atomic before/after history for actual data, including bulk updates and deletion."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
from uuid import uuid4
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, router, transaction

_context = ContextVar('fact_history_actor', default=None)
_ignored = {'id', 'created_at', 'updated_at', 'revision'}


@contextmanager
def history_actor(user, reason=''):
    previous = _context.get()
    actor = user if user is not None and getattr(user, 'is_authenticated', False) else None
    group = previous['group'] if previous and previous['actor'] == actor else uuid4()
    token = _context.set({'actor': actor, 'group': group, 'reason': reason or (previous['reason'] if previous else '')})
    try:
        yield
    finally:
        _context.reset(token)


def with_history_actor(function):
    @wraps(function)
    def wrapped(user, *args, **kwargs):
        with history_actor(user):
            return function(user, *args, **kwargs)
    return wrapped


def snapshot(instance):
    fields = {f.attname: getattr(instance, f.attname) for f in instance._meta.concrete_fields}
    references = {}
    for field in instance._meta.concrete_fields:
        if field.is_relation and field.name != 'company' and fields.get(field.attname) is not None:
            target = getattr(instance, field.name)
            references[field.attname] = str(target)
            if field.related_model._meta.label_lower == 'accounts.user':
                references[field.attname] = target.get_full_name() or target.username
    obj_id = getattr(instance, 'construction_object_id', None)
    day = getattr(instance, 'date', None) or getattr(instance, 'effective_date', None) or getattr(instance, 'arrived_on', None)
    if hasattr(instance, 'project_work_id') and instance.project_work_id:
        obj_id = instance.project_work.section.construction_object_id
    if hasattr(instance,'assignment_id') and instance.assignment_id:
        obj_id = instance.assignment.construction_object_id
    if instance._meta.label_lower == 'production.employeeworklink':
        obj_id = instance.activity.construction_object_id; day = instance.activity.date
    camp_ids = [getattr(instance, name) for name in ('camp_id', 'from_camp_id', 'to_camp_id') if getattr(instance, name, None)]
    if instance._meta.label_lower=='personnel.lodgingbed':camp_ids.append(instance.building.camp_id)
    label=str(instance)
    if instance._meta.label_lower in {'production.equipmentcampstay','production.equipmentdayrecord','production.equipmenttransportevent'}:
        label=str(instance.equipment)+' · '+str(day)
    elif instance._meta.label_lower in {'personnel.campstay','personnel.housingevent'}:
        label=str(instance.person)+' · '+str(day)
    elif instance._meta.label_lower=='production.employeeworklink':
        label=str(instance.activity.employee)+' → '+str(instance.work)
    return json.loads(json.dumps({'fields': fields, 'references': references, 'label': label,
        'object_id': obj_id, 'camp_ids': sorted(set(camp_ids)), 'date': day}, cls=DjangoJSONEncoder))


def _different(before, after):
    if before is None or after is None:
        return True
    return {k:v for k,v in before['fields'].items() if k not in _ignored} != {k:v for k,v in after['fields'].items() if k not in _ignored}


def append_revision(instance, before, after, using):
    if not _different(before, after):
        return
    from apps.production.history_models import FactRevision
    ctx = _context.get() or {'actor': None, 'group': uuid4(), 'reason': ''}
    actor = ctx['actor']
    from core.impersonation import context as acting_context
    acting = acting_context.get()
    if acting: actor = acting['actor']
    if actor and not actor.is_superuser and actor.company_id != instance.company_id:
        raise ValueError('Автор изменения и фактическая запись должны относиться к одной компании.')
    metadata = after or before
    model = instance._meta.label_lower
    direction = 'mechanization' if model in {'production.equipmentactivity','production.equipmentfact','production.fuelfact','production.campfuelday','resources.transportimportrow','production.equipmentdaydetails','production.equipmentdayrecord','production.equipmenttransportevent','production.equipmentcampstay','production.equipmentassignment','production.equipmentstatechange'} else 'labor' if model.startswith('personnel.') or model == 'production.laborfact' else 'works'
    if model.startswith('personnel.lodging'):direction='lodging'
    if model=='production.employeeactivity':
        from apps.production.employee_pool import activity_direction
        direction=activity_direction(instance)
    return FactRevision.objects.using(using).create(company_id=instance.company_id, entity_type=model, entity_id=str(instance.pk),
        operation='CREATE' if before is None else 'DELETE' if after is None else 'UPDATE', before=before, after=after,
        actor=actor, actor_name=(((actor.get_full_name() or actor.username) + (' (в режиме '+acting['target'].username+')' if acting else ''))[:300]) if actor else 'Система / импорт',
        actor_role='ADMIN' if actor and actor.is_superuser else actor.role.code if actor and actor.role_id else '',
        group_id=ctx['group'], reason=ctx['reason'], direction=direction, fact_date=metadata['date'],
        object_id=metadata['object_id'], camp_ids=metadata['camp_ids'], label=metadata['label'])


class ActualHistoryQuerySet(models.QuerySet):
    def update(self, **kwargs):
        using = self.db
        with transaction.atomic(using=using):
            rows = list(self.model._base_manager.using(using).filter(pk__in=self.values('pk')).select_for_update().order_by('pk'))
            before = {row.pk: snapshot(row) for row in rows}
            count = super().update(**kwargs)
            for row in self.model._base_manager.using(using).filter(pk__in=before).order_by('pk'):
                append_revision(row, before[row.pk], snapshot(row), using)
            return count

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False, update_fields=None, unique_fields=None):
        if ignore_conflicts or update_conflicts:
            raise ValueError('Для фактических записей используйте сохранение с историей без подавления конфликтов.')
        with transaction.atomic(using=self.db):
            rows = super().bulk_create(objs, batch_size=batch_size, update_fields=update_fields, unique_fields=unique_fields)
            if any(row.pk is None for row in rows):
                raise ValueError('База данных не вернула идентификаторы факта для сохранения истории.')
            for row in rows:
                append_revision(row, None, snapshot(row), self.db)
            return rows


class ActualHistoryMixin(models.Model):
    objects = models.Manager.from_queryset(ActualHistoryQuerySet)()
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        using = kwargs.get('using') or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=using):
            previous = type(self)._base_manager.using(using).select_for_update().filter(pk=self.pk).first() if self.pk else None
            before = snapshot(previous) if previous else None
            result = super().save(*args, **kwargs)
            persisted = type(self)._base_manager.using(using).get(pk=self.pk)
            append_revision(persisted, before, snapshot(persisted), using)
            return result


def record_deletion(sender, instance, using, origin=None, **kwargs):
    # Django's deletion collector sends this inside the same transaction as the
    # deletion, including queryset deletion and cascades from works or people.
    current = sender._base_manager.using(using).select_for_update().get(pk=instance.pk)
    append_revision(current, snapshot(current), None, using)
