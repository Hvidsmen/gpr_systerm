"""Search resource names and their explicit Excel associations without changing identities."""
from django.db.models import prefetch_related_objects
import unicodedata


def search_key(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value or '')).split()).casefold().replace('ё','е')


def filter_name(queryset, value):
    query=search_key(value)
    if not query:
        return queryset
    # Python comparison gives SQLite and PostgreSQL the same Unicode matching.
    # Iterate only the already company-scoped rows and return the original queryset
    # so its annotations, ordering and all other filters are preserved.
    ids=[pk for pk,name,aliases in queryset.prefetch_related(None).values_list('pk','name','excel_aliases').iterator(chunk_size=1000)
         if any(query in search_key(candidate) for candidate in [name,*(aliases or [])])]
    return queryset.filter(pk__in=ids)


def alias_options(options):
    """Keep aliases in escaped option attributes, separate from visible labels."""
    from .models import Brigade, EquipmentType, Position
    positions=[option['value'].instance for option in options
               if isinstance(getattr(option.get('value'),'instance',None),Position)]
    if positions:
        prefetch_related_objects(positions,'planning_mapping__brigade')
    for option in options:
        item=getattr(option.get('value'),'instance',None)
        if isinstance(item,Position):
            mapping=getattr(item,'planning_mapping',None)
            item=mapping.brigade if mapping and mapping.company_id==item.company_id and mapping.brigade.company_id==item.company_id else None
        if isinstance(item,(Brigade,EquipmentType)) and item.excel_aliases:
            option['attrs']['data-search-aliases']=' '.join(item.excel_aliases)
    return options
