"""Keep manually merged unit spellings canonical in later catalogue writes."""
def aliases(company,using='default'):
    from .models import MeasurementUnitAlias,MeasurementUnit
    result=dict(MeasurementUnitAlias.objects.using(using).filter(company=company).values_list('symbol','target__symbol'))
    if result:
        current=set(MeasurementUnit.objects.using(using).filter(company=company).values_list('symbol',flat=True))
        for source,target in list(result.items()):
            cleaned=source.rstrip(' .')
            if cleaned not in current:result.setdefault(cleaned,target)
    return result


def canonical(company,symbol,using='default'):
    from .models import MeasurementUnitAlias
    return MeasurementUnitAlias.objects.using(using).filter(company=company,symbol=symbol).values_list('target__symbol',flat=True).first() or symbol
