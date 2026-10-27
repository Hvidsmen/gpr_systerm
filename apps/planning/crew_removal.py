"""Explicit, plan-local removal of a position and its captured machine links."""
import json
from copy import deepcopy
from django.core.exceptions import ValidationError
from apps.audit.models import AuditLog


def removal_actions(raw):
    try:
        actions = json.loads(raw or '{}') if isinstance(raw, str) else raw or {}
        if not isinstance(actions, dict):
            raise ValueError
        for brigade, action in actions.items():
            if not str(brigade).isdigit() or not isinstance(action, dict):
                raise ValueError
            if action.get('mode') not in {'detach', 'remove_equipment'}:
                raise ValueError
            if 'order' in action and (type(action['order']) is not int or action['order'] < 0):
                raise ValueError
            equipment = action.get('equipment')
            if not isinstance(equipment, dict) or any(not str(pk).isdigit() for pk in equipment):
                raise ValueError
        return {str(pk): action for pk, action in actions.items()}
    except (ValueError, TypeError):
        raise ValidationError('Неверный вариант удаления экипажа. Обновите страницу.')


def reviewed_action(brigade, machines, actions, *, daily=False):
    action = actions.get(str(brigade))
    if not machines:
        if action and action['equipment']:
            raise ValidationError('Связи с техникой изменились. Обновите страницу и повторите удаление.')
        return None
    if not action:
        raise ValidationError('Должность входит в экипаж техники. Выберите: удалить вместе с техникой или оставить технику.')
    expected = {str(machine.pk): str(machine.revision) if daily else '' for machine in machines}
    if action['equipment'] != expected:
        raise ValidationError('Связанная техника уже изменена. Обновите страницу и повторите удаление.')
    return action['mode']


def audit(user, machine, mode, daily):
    AuditLog.objects.create(
        user=user, action='DELETE' if mode == 'remove_equipment' else 'UPDATE',
        entity_type='planning.dailyscheduleentry' if daily else 'planning.resourcemonthallocation',
        entity_id=str(machine.pk),
        old_value={'kind': 'equipment', 'label': machine.label if daily else str(machine.equipment_type)},
        comment='Удалена техника вместе с должностью экипажа' if mode == 'remove_equipment' else 'Снята связь должности с техникой в текущем плане',
    )


def monthly(user, rows, removed_brigades, raw):
    from .table_resource_crew import recipe
    actions = removal_actions(raw)
    removed = set(removed_brigades)
    # Preserve the order of the user's decisions when two positions share machines.
    order = [int(pk) for pk in sorted(actions,key=lambda pk:actions[pk].get('order',0)) if int(pk) in removed]
    order += sorted(removed - set(order))
    sources = {row.pk: row for row in rows if row.kind == 'equipment'}
    for brigade in order:
        recipes = {pk: recipe(user.company, row.equipment_type, row.crew_data) for pk, row in sources.items()}
        machines = [row for pk, row in sources.items() if any(m['brigade_id'] == brigade for m in recipes[pk])]
        mode = reviewed_action(brigade, machines, actions)
        for machine in machines:
            audit(user, machine, mode, False)
            if mode == 'remove_equipment':
                del sources[machine.pk]
                machine.delete()
            else:
                machine.crew_data = {**machine.crew_data, 'members': [m for m in recipes[machine.pk] if m['brigade_id'] != brigade]}
                machine.save(update_fields=['crew_data', 'updated_at'])


def daily(user, schedule, brigade, raw):
    from .table_resource_crew import daily_crew_input
    sources = list(schedule.entries.select_for_update().filter(kind='equipment'))
    recipes = {row.pk: daily_crew_input(row) for row in sources}
    machines = [row for row in sources if any(m['brigade_id'] == brigade for m in recipes[row.pk])]
    mode = reviewed_action(brigade, machines, removal_actions(raw), daily=True)
    for machine in machines:
        audit(user, machine, mode, True)
        if mode == 'remove_equipment':
            machine.delete()
        else:
            machine.metadata = {**deepcopy(machine.metadata), 'table_crew': [m for m in recipes[machine.pk] if m['brigade_id'] != brigade]}
            machine.revision += 1
            machine.save(update_fields=['metadata', 'revision'])
