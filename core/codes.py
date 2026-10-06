"""Allocate readable codes without reusing deleted records' numbers."""

PREFIXES = {
    'projects.project': 'PRJ', 'projects.constructionobject': 'OBJ',
    'projects.section': 'SEC', 'works.projectwork': 'WORK',
    'works.worktemplate': 'TPL', 'planning.loadprofile': 'PROF',
    'planning.productioncalendar': 'CAL', 'resources.position': 'POS',
    'resources.brigade': 'BR', 'resources.fueltype': 'FUEL',
    'production.deviationreason': 'REASON',
}


def allocate_code(instance, using):
    from apps.accounts.models import Company
    from .models import CodeSequence
    label = instance._meta.label_lower
    field = instance._meta.get_field('code')
    # Lock a stable company row before creating a sequence. Globally unique
    # dictionaries share a lock across companies to avoid concurrent collisions.
    companies = Company.objects.using(using).select_for_update()
    if field.unique:
        companies.order_by('pk').first()
    else:
        companies.get(pk=instance.company_id)
    sequence, _ = CodeSequence.objects.using(using).get_or_create(
        company_id=instance.company_id, model_label=label,
    )
    sequence = CodeSequence.objects.using(using).select_for_update().get(pk=sequence.pk)
    existing = type(instance)._default_manager.using(using).all()
    if not field.unique:
        existing = existing.filter(company_id=instance.company_id)
    while True:
        sequence.last_number += 1
        code = f'{PREFIXES[label]}-{sequence.last_number:06d}'
        if not existing.filter(code=code).exists():
            break
    sequence.save(using=using, update_fields=['last_number'])
    return code
