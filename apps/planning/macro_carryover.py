"""Only explicitly connected, immutable history can supply sub-object carryovers."""
from copy import deepcopy
from decimal import Decimal
from django.core.exceptions import ValidationError
from apps.projects.models import Section
from apps.works.models import ProjectWork
from apps.works.prices import rate_on
from .models import MacroHistorySource,MacroCarryoverWork


def history_links(version,month):
    from .carryover import previous_period
    start,end=previous_period(month)
    return MacroHistorySource.objects.filter(company_id=version.company_id,workspace_id=version.workspace_id,start__lte=start,end__gte=end).select_related('construction_object','review__version').defer('review__snapshot','review__version__snapshot').order_by('construction_object__name') if version.workspace_id else MacroHistorySource.objects.none()


def remap(data,mapping):
    result=deepcopy(data);spec=result['spec']
    result['work_id']=mapping.target_work_id
    if set(data['items'])-set(mapping.item_mapping):
        raise ValidationError('Изменился состав перенесенной работы. Проверьте сопоставление подработ.')
    result['items']={str(mapping.item_mapping[k]):v for k,v in data['items'].items()}
    spec['id']=mapping.target_work_id
    for item in spec.get('items',[]):
        if str(item['id']) not in mapping.item_mapping:raise ValidationError('Изменился состав перенесенной работы. Проверьте сопоставление подработ.')
        item['id']=mapping.item_mapping[str(item['id'])]
    return result


def ensure_work(user,version,month,data):
    source=ProjectWork.objects.select_related('section__construction_object').prefetch_related('items').get(pk=data['source_work_id'],company=user.company)
    if source.section.construction_object_id==version.construction_object_id:return data
    allowed=history_links(version,month).filter(review__version_id=data['source_version_id'],construction_object_id=source.section.construction_object_id).exists()
    if not allowed:raise ValidationError('Работа не входит в подключенную историю этого макрообъекта.')
    mapping=MacroCarryoverWork.objects.filter(company=user.company,workspace_id=version.workspace_id,source_work=source).first()
    # preview has already remapped an existing mapping.
    if mapping and data['work_id']==mapping.target_work_id:return data
    if mapping:return remap(data,mapping)
    spec=data['spec']
    section_name=f'Остатки · {source.section.construction_object.name} · {source.section.name}'[:255]
    section=Section.objects.filter(company=user.company,construction_object_id=version.construction_object_id,name=section_name).first()
    if not section:section=Section.objects.create(company=user.company,construction_object_id=version.construction_object_id,name=section_name)
    work=deepcopy(source);work.pk=None;work.code='';work.section=section;work.template_id=None;work._state.adding=True
    work.name=spec['name'];work.unit=spec['unit'];work.kind=spec['kind'];work.allow_fractional=spec['allow_fractional']
    work.unit_price=rate_on(spec.get('revenue_prices',[]),month,Decimal(spec.get('unit_price') or 0));work._price_actor=user;work.save()
    frozen={i['id']:i for i in spec.get('items',[])};item_mapping={};parents=[]
    for original in source.items.all():
        item=frozen.get(original.pk)
        if item is None:continue
        clone=deepcopy(original);clone.pk=None;clone.project_work=work;clone.parent_id=None;clone._state.adding=True
        clone.name=item['name'];clone.unit=item['unit'];clone.quantity_per_unit=Decimal(item['norm']);clone.weight=Decimal(item['weight']);clone.save()
        item_mapping[str(original.pk)]=clone.pk;parents.append((clone,original.parent_id))
    for clone,parent in parents:
        if parent:clone.parent_id=item_mapping.get(str(parent));clone.save(update_fields=['parent'])
    mapping=MacroCarryoverWork.objects.create(company=user.company,workspace_id=version.workspace_id,source_work=source,target_work=work,item_mapping=item_mapping)
    return remap(data,mapping)
