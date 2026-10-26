"""One shared resource allocation can reference several leaf works, counted once."""
from collections import defaultdict
from django.core.exceptions import ValidationError
from apps.works.models import ProjectWork, ProjectWorkItem


def identities(assignment):
    result = [(assignment.work_id, assignment.work_item_id)] if assignment.work_id else []
    if assignment.pk:
        result.extend((row.work_id, row.work_item_id) for row in assignment.extra_targets.all())
    return result


def normalize(owner, targets):
    """Validate identities against the same company/object; reject package-level links."""
    result = []
    for target in targets:
        try:
            work_id, item_id = target
            if isinstance(work_id, bool) or isinstance(item_id, bool):raise ValueError
            work_id = int(work_id)
            item_id = int(item_id) if item_id not in (None, '') else None
            if work_id <= 0 or item_id is not None and item_id <= 0:raise ValueError
        except (TypeError, ValueError):
            raise ValidationError('Некорректная привязка ресурса к работе.')
        if (work_id, item_id) in result:raise ValidationError('Одна работа выбрана несколько раз.')
        result.append((work_id, item_id))
    works = {w.pk:w for w in ProjectWork.objects.filter(company_id=owner.company_id,
        section__construction_object_id=owner.construction_object_id, pk__in=[w for w, _ in result])}
    items = {i.pk:i for i in ProjectWorkItem.objects.filter(company_id=owner.company_id, pk__in=[i for _, i in result if i])}
    for work_id, item_id in result:
        work = works.get(work_id)
        if not work:raise ValidationError('Все выбранные работы должны принадлежать этому объекту и компании.')
        if item_id:
            item = items.get(item_id)
            if not item or item.project_work_id != work_id:raise ValidationError('Работа в составе комплекса принадлежит другому комплексу.')
        elif work.kind == 'COMPOSITE':
            raise ValidationError('Выберите работу в составе комплекса, а не комплекс целиком.')
    return sorted(result, key=lambda pair:(pair[0], pair[1] or 0))


def replace(assignment, targets):
    """Caller holds the owner lock and has already validated all targets."""
    from .models import WorkResourceTarget
    assignment.extra_targets.all().delete()
    WorkResourceTarget.objects.bulk_create([WorkResourceTarget(assignment=assignment,
        work_id=work, work_item_id=item) for work, item in targets[1:]])
    assignment._prefetched_objects_cache = {}


def describe(assignment):
    targets = []
    if assignment.work_id:
        targets.append((assignment.work, assignment.work_item))
    if assignment.pk:
        targets.extend((link.work, link.work_item) for link in assignment.extra_targets.all())
    result = []
    for work, item in targets:
        label = f'{work.name} → {item.name}' if item else work.name
        if work.complex_object_id:label = f'{work.complex_object.name} → {label}'
        result.append({'work_id':work.pk, 'work_item_id':item.pk if item else None,
            'label':label, 'work_name':work.name, 'item_name':item.name if item else ''})
    return result


def assignment_values(owner, fields=None):
    """Keep the exact old fingerprint format for unchanged single-target records."""
    rows = list(owner.resource_assignments.order_by('pk').values(*(['pk', *fields] if fields else [])))
    from .models import WorkResourceTarget
    extra = defaultdict(list)
    for link in WorkResourceTarget.objects.filter(assignment_id__in=[r.get('pk',r.get('id')) for r in rows]).order_by('work_id','work_item_id'):
        extra[link.assignment_id].append({'work_id':link.work_id,'work_item_id':link.work_item_id})
    for row in rows:
        pk = row.pop('pk') if fields else row['id']
        if extra[pk]:
            row['work_targets'] = [{'work_id':row['work_id'],'work_item_id':row['work_item_id']}, *extra[pk]]
    return rows
