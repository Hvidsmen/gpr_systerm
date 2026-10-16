"""Delete monthly allocations only when all their stored volumes are zero."""
from decimal import Decimal, InvalidOperation


def zero_value(value):
    try:
        return Decimal(str(value or '0')).is_zero()
    except InvalidOperation:
        return False


def zero_work(row):
    if not zero_value(row.quantity):
        return False
    if row.item_quantities and not all(zero_value(v) for v in row.item_quantities.values()):
        return False
    for day in row.daily_override or []:
        if not zero_value(day.get('quantity')):
            return False
        for item in day.get('items', []):
            if not zero_value(item.get('quantity')):
                return False
    return True


def remove_zero_works(version, month):
    rows = version.work_allocations.filter(month=month, quantity=0)
    ids = [row.pk for row in rows if zero_work(row)]
    version.work_allocations.filter(month=month, pk__in=ids).delete()
    return len(ids)
