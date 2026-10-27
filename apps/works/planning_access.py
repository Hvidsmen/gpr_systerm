"""Access to legacy catalogue entries when adding work to a plan.

Existing plan rows keep their captured composition. This policy applies to new
selections, including whole branches, rather than hiding historical plan data.
"""
from django.core.exceptions import ValidationError
from core.permissions import role_code


def can_select_legacy(user):
    return user is not None and role_code(user) == 'ADMIN'


def planning_works(queryset, user, *, whole=False):
    if can_select_legacy(user):
        return queryset
    queryset = queryset.filter(is_legacy_structure=False)
    if whole:
        # These older pickers cannot express a partial package.
        queryset = queryset.exclude(items__is_legacy_structure=True)
    return queryset


def allowed_item_ids(work, user):
    items = list(work.items.all())
    blocked = {item.pk for item in items if item.is_legacy_structure} if not can_select_legacy(user) else set()
    # Selecting a descendant must not bypass a marked parent branch.
    while True:
        expanded = blocked | {item.pk for item in items if item.parent_id in blocked}
        if expanded == blocked:
            return {item.pk for item in items} - blocked
        blocked = expanded


def planning_selection(work, selected, user):
    if can_select_legacy(user):
        return selected
    if work.is_legacy_structure:
        raise ValidationError('Эта работа недоступна для добавления в план.')
    all_ids = {item.pk for item in work.items.all()}
    allowed = allowed_item_ids(work, user)
    if selected:
        if not set(selected) <= allowed:
            raise ValidationError('Выбранный состав содержит недоступные для планирования работы.')
        return selected
    if all_ids and not allowed:
        raise ValidationError('В комплексе нет доступных для планирования работ.')
    return [] if allowed == all_ids else sorted(allowed)


def validate_plan_addition(work, selected, user, existing=None):
    """Permit edits of saved rows, but reject newly introduced restricted IDs."""
    if can_select_legacy(user):
        return
    if existing is None:
        allowed = planning_selection(work, selected, user)
        if allowed != selected:
            raise ValidationError('Выберите доступные работы комплекса по отдельности.')
        return
    if work.pk != existing.work_id:
        return validate_plan_addition(work, selected, user)
    all_ids = {item.pk for item in work.items.all()}
    before = set(existing.selected_item_ids) if existing.selected_item_ids else all_ids
    after = set(selected) if selected else all_ids
    allowed = set() if work.is_legacy_structure else allowed_item_ids(work, user)
    if (after - before) - allowed:
        raise ValidationError('Нельзя добавить недоступные для планирования работы.')


def component_options(works, allocations, user):
    """Editor choices include saved items, and only permitted new items."""
    existing = {row.work_id: row for row in allocations}
    result = {}
    for work in works:
        row = existing.get(work.pk)
        if work.is_legacy_structure and not can_select_legacy(user) and row is None:
            continue
        items = list(work.items.all())
        allowed = allowed_item_ids(work, user) if not work.is_legacy_structure or can_select_legacy(user) else set()
        if row is not None:
            allowed |= set(row.selected_item_ids) if row.selected_item_ids else {item.pk for item in items}
        result[str(work.pk)] = [
            {'id': item.pk, 'name': item.name, 'unit': item.unit, 'norm': str(item.quantity_per_unit)}
            for item in items if item.company_id == work.company_id and item.pk in allowed
        ]
    return result
