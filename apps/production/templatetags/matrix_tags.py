from django import template

register = template.Library()


@register.filter
def get_plan(plans_by_date, date):
    """Получить план по дате из словаря."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return plans_by_date.get(date_str)


@register.filter
def get_plan_id(plans_by_date, date):
    """Получить ID плана по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    plan = plans_by_date.get(date_str)
    return plan.pk if plan else ''


@register.filter
def total_workers(plans_by_date):
    """Посчитать общее количество человек по всем датам."""
    total = 0
    for plan in plans_by_date.values():
        total += plan.planned_workers or 0
    return total


@register.filter
def average_workers(plans_by_date):
    """Посчитать среднее количество человек по всем датам с планами."""
    total = 0
    count = 0
    for plan in plans_by_date.values():
        if plan.planned_workers is not None and plan.planned_workers > 0:
            total += plan.planned_workers
            count += 1

    if count == 0:
        return 0

    # Возвращаем среднее, округлённое до 1 знака
    return round(total / count, 1)


@register.filter
def average_hours(plans_by_date):
    """Посчитать среднее количество чел-часов по всем датам с планами."""
    total = 0
    count = 0
    for plan in plans_by_date.values():
        if plan.planned_hours is not None and plan.planned_hours > 0:
            total += plan.planned_hours
            count += 1

    if count == 0:
        return 0

    return round(total / count, 1)


@register.filter
def average_rate(plans_by_date):
    """Посчитать среднюю ставку по всем датам с планами."""
    total = 0
    count = 0
    for plan in plans_by_date.values():
        if plan.hourly_rate is not None and plan.hourly_rate > 0:
            total += plan.hourly_rate
            count += 1

    if count == 0:
        return 0

    return round(total / count, 2)

@register.filter
def get_equipment_plan(plans_by_date, date):
    """Получить план по технике по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return plans_by_date.get(date_str)


@register.filter
def get_equipment_plan_id(plans_by_date, date):
    """Получить ID плана по технике по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    plan = plans_by_date.get(date_str)
    return plan.pk if plan else ''


@register.filter
def total_equipment(plans_by_date):
    """Сумма единиц техники по всем датам."""
    total = 0
    for plan in plans_by_date.values():
        total += plan.planned_count or 0
    return total


@register.filter
def average_equipment(plans_by_date):
    """Среднее количество единиц техники в день."""
    total = 0
    count = 0
    for plan in plans_by_date.values():
        if plan.planned_count is not None and plan.planned_count > 0:
            total += plan.planned_count
            count += 1
    if count == 0:
        return 0
    return round(total / count, 1)


@register.filter
def get_fuel_plan(plans_by_date, date):
    """Получить план по ГСМ по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return plans_by_date.get(date_str)


@register.filter
def get_fuel_plan_id(plans_by_date, date):
    """Получить ID плана по ГСМ по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    plan = plans_by_date.get(date_str)
    return plan.pk if plan else ''


@register.filter
def total_fuel(plans_by_date):
    """Сумма литров по всем датам."""
    total = 0
    for plan in plans_by_date.values():
        total += plan.planned_liters or 0
    return total


@register.filter
def average_fuel(plans_by_date):
    """Среднее литров в день."""
    total = 0
    count = 0
    for plan in plans_by_date.values():
        if plan.planned_liters is not None and plan.planned_liters > 0:
            total += plan.planned_liters
            count += 1
    if count == 0:
        return 0
    return round(total / count, 1)

@register.filter
def get_labor_fact(facts_by_date, date):
    """Получить факт по людям по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return facts_by_date.get(date_str)


@register.filter
def get_labor_fact_id(facts_by_date, date):
    """Получить ID факта по людям по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    fact = facts_by_date.get(date_str)
    return fact.pk if fact else ''


@register.filter
def total_actual_workers(facts_by_date):
    """Сумма фактических человек по всем датам."""
    total = 0
    for fact in facts_by_date.values():
        total += fact.actual_workers or 0
    return total


@register.filter
def average_actual_workers(facts_by_date):
    """Среднее фактических человек в день."""
    total = 0
    count = 0
    for fact in facts_by_date.values():
        if fact.actual_workers is not None and fact.actual_workers > 0:
            total += fact.actual_workers
            count += 1
    if count == 0:
        return 0
    return round(total / count, 1)


@register.filter
def get_equipment_fact(facts_by_date, date):
    """Получить факт по технике по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return facts_by_date.get(date_str)


@register.filter
def get_equipment_fact_id(facts_by_date, date):
    """Получить ID факта по технике по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    fact = facts_by_date.get(date_str)
    return fact.pk if fact else ''


@register.filter
def total_actual_equipment(facts_by_date):
    """Сумма фактических единиц техники по всем датам."""
    total = 0
    for fact in facts_by_date.values():
        total += fact.actual_count or 0
    return total


@register.filter
def average_actual_equipment(facts_by_date):
    """Среднее фактических единиц техники в день."""
    total = 0
    count = 0
    for fact in facts_by_date.values():
        if fact.actual_count is not None and fact.actual_count > 0:
            total += fact.actual_count
            count += 1
    if count == 0:
        return 0
    return round(total / count, 1)


@register.filter
def get_fuel_fact(facts_by_date, date):
    """Получить факт по ГСМ по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    return facts_by_date.get(date_str)


@register.filter
def get_fuel_fact_id(facts_by_date, date):
    """Получить ID факта по ГСМ по дате."""
    date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)
    fact = facts_by_date.get(date_str)
    return fact.pk if fact else ''


@register.filter
def total_actual_fuel(facts_by_date):
    """Сумма фактических литров по всем датам."""
    total = 0
    for fact in facts_by_date.values():
        total += fact.actual_liters or 0
    return total


@register.filter
def average_actual_fuel(facts_by_date):
    """Среднее фактических литров в день."""
    total = 0
    count = 0
    for fact in facts_by_date.values():
        if fact.actual_liters is not None and fact.actual_liters > 0:
            total += fact.actual_liters
            count += 1
    if count == 0:
        return 0
    return round(total / count, 1)