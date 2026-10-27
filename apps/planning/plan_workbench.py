"""A common presentation contract for monthly GPR and daily SMG editors.

GPR keeps its existing formset and complex-quantity engine as the write model.
The visible matrix binds to those fields; it does not introduce another store.
"""
import json
from collections import defaultdict
from decimal import Decimal,InvalidOperation
from types import SimpleNamespace
from django.core.exceptions import ValidationError
from django.db import transaction
from core.permissions import PLAN_ROLES,require_roles,require_planning_object
from .models import DailyScheduleEntry,GlobalPlanVersion,ResourceMonthAllocation

ZERO=Decimal(0)
LABELS={'work':'Работы','labor':'Должности','equipment':'Техника','fuel':'ГСМ'}


def decimal(value):
    try:return Decimal(str(value or 0).replace(',','.'))
    except InvalidOperation:return ZERO


def catalogue_options(works,choices):
    parents={w.pk:w for w in works.select_related('complex_object')}
    item_parents={i.pk:w.pk for w in works.prefetch_related('items') for i in w.items.all()}
    result=[]
    for _,group in choices:
        if not isinstance(group,(list,tuple)):continue
        for token,label in group:
            kind,pk=token.split(':');pk=int(pk)
            work=parents.get(pk) if kind=='package' else parents.get(item_parents.get(pk)) if kind=='item' else None
            level='object' if kind=='object' else 'package' if kind=='package' and work.kind=='COMPOSITE' else 'work'
            result.append({'value':token,'label':label,'level':level,'object_id':str(pk) if kind=='object' else str(work.complex_object_id or '') if work else '',
                'package_id':str(work.pk) if work and work.kind=='COMPOSITE' else ''})
    return result


def locate_work(entry,work):
    """Prefer captured labels; use the catalogue to complete older SMG metadata."""
    metadata=entry.metadata
    section_id=metadata.get('section_id') or (work.section_id if work else None)
    section_name=metadata.get('section_name') or (work.section.name if work else 'Без раздела')
    object_id=metadata.get('complex_object_id') or (work.complex_object_id if work else None)
    object_name=metadata.get('complex_object_name') or (work.complex_object.name if work and work.complex_object_id else 'Самостоятельные работы')
    entry.hierarchy_section_key='section:'+str(section_id or 'none')
    entry.hierarchy_section_name=section_name
    entry.hierarchy_object_key=entry.hierarchy_section_key+':object:'+str(object_id or 'none')
    entry.hierarchy_object_name=object_name
    entry.hierarchy_has_object=bool(object_id)
    entry.hierarchy_path=' → '.join([section_name,object_name,entry.label])


def attach_work_hierarchy(sections):
    """Group by identity, never merge equally named objects from different sections."""
    for section in sections:
        if section['kind']!='work':continue
        roots={}
        for group in section['groups']:
            for entry in group['entries']:
                root=roots.setdefault(entry.hierarchy_section_key,{'key':entry.hierarchy_section_key,'label':entry.hierarchy_section_name,'objects':{}})
                obj=root['objects'].setdefault(entry.hierarchy_object_key,{'key':entry.hierarchy_object_key,'label':entry.hierarchy_object_name,'has_object':entry.hierarchy_has_object,'branches':[]})
                count=sum(bool(row.get('item_id_value')) for row in entry.comparison)
                obj['branches'].append({'key':'work:'+str(entry.metadata.get('id') or entry.pk),'label':entry.label,'entries':[entry],
                    'is_package':entry.metadata.get('kind')=='COMPOSITE','count':count,'work_count':count or 1})
        tree=[]
        for root in roots.values():
            root['objects']=sorted(root['objects'].values(),key=lambda obj:(obj['label'].casefold(),obj['key']))
            for obj in root['objects']:
                obj['branches'].sort(key=lambda b:(b['label'].casefold(),b['key']))
                obj['count']=sum(b['work_count'] for b in obj['branches'])
            root['count']=sum(obj['count'] for obj in root['objects'])
            tree.append(root)
        section['hierarchy']=sorted(tree,key=lambda node:(node['label'].casefold(),node['key']))


def month_matrix(version,month,work_forms,resource_forms):
    grouped={kind:defaultdict(list) for kind in LABELS}
    from apps.resources.models import Brigade
    from .table_resource_crew import display_recipe
    brigades={b.pk:b for b in Brigade.objects.filter(company=version.company).select_related('group')}
    def labor_group(brigade_id):
        b=brigades.get(brigade_id)
        return str(b.group) if b and b.group_id else 'Без группы'
    def measure(label,value,field='',item=None,digits=6,norm=''):
        return {'label':label,'after':value,'before':ZERO,'delta':ZERO,'field':field,'item_id_value':item or '',
            'step':str(Decimal(1).scaleb(-digits)),'cells':[],'month_input':bool(field),'norm':norm}
    for form in work_forms:
        row=form.instance
        if not row.work_id:continue
        work=row.work;items=list(work.items.all());selected=set(form['selected_item_ids'].value() or [])
        if isinstance(form['selected_item_ids'].value(),str):
            try:selected=set(json.loads(form['selected_item_ids'].value()) or [])
            except (ValueError,TypeError):selected=set(row.selected_item_ids)
        included=[i for i in items if not selected or i.pk in selected]
        values=row.item_quantities
        entered=form['edited_item_quantities'].value()
        if entered:
            try:
                parsed=json.loads(entered) if isinstance(entered,str) else entered
                if isinstance(parsed,dict) and parsed:values=parsed
            except (ValueError,TypeError):pass
        quantity=form['quantity'].value()
        entry=SimpleNamespace(pk='work:'+form.prefix,label=work.name,kind='work',native_prefix=form.prefix,work_id=work.pk,
            path=(work.complex_object.name+' → ' if work.complex_object_id else '')+work.name,metadata={'id':work.pk,'kind':work.kind},reference='',can_edit=True,comparison=[],
            errors=[str(error) for errors in form.errors.values() for error in errors],profile=True,incomplete=work.kind=='COMPOSITE' and len(included)<len(items))
        if work.kind=='COMPOSITE':
            entry.comparison.append(measure('Готовые комплексы · '+work.unit,quantity))
            entry.comparison[0]['computed_source']=form.add_prefix('quantity')
            for item in included:
                value=values.get(str(item.pk),'0') if values else decimal(quantity)*item.quantity_per_unit
                entry.comparison.append(measure(item.name+' · '+item.unit,value,'quantity',item.pk,norm=item.quantity_per_unit))
        else:entry.comparison.append(measure('Объём · '+work.unit,quantity,'quantity',digits=3))
        locate_work(entry,work)
        group=work.complex_object.name if work.complex_object_id else 'Самостоятельные работы'
        grouped['work'][group].append(entry)
    for form in resource_forms:
        row=form.instance;kind=row.kind
        if kind not in LABELS or kind=='work':continue
        name=str(row.brigade if kind=='labor' else row.equipment_type if kind=='equipment' else row.get_fuel_type_display())
        entry=SimpleNamespace(pk='resource:'+form.prefix,label=name,kind=kind,native_prefix=form.prefix,path=name,metadata={},reference=row.equipment_number,can_edit=not any(f.disabled for f in form.fields.values()),
            comparison=[],errors=[str(error) for errors in form.errors.values() for error in errors])
        fields=[('Средняя численность, чел.' if kind=='labor' else 'Среднее количество, ед.','count',0)] if kind!='fuel' else [('Расход, л','liters',2),('Остаток, л','balance',2)]
        for label,field,digits in fields:entry.comparison.append(measure(label,form[field].value(),field,digits=digits))
        entry.brigade_id=row.brigade_id or ''
        entry.crew_recipe_json=json.dumps(display_recipe(version.company,row.equipment_type,row.crew_data)) if kind=='equipment' else '[]'
        entry.crew_counts_json=json.dumps({'month':row.crew_data.get('count',0)}) if kind=='labor' else '{}'
        entry.crew_note='Численность включает экипаж техники. Можно добавить людей сверх экипажа.' if kind=='labor' and row.crew_data.get('count') else ''
        grouped[kind][labor_group(row.brigade_id) if kind=='labor' else 'Общие ресурсы объекта'].append(entry)
    # Captured assignments are shown once in the same summary as manual inputs.
    # Their dates, crew and quantities keep being edited by the existing service.
    from .work_resources import owner_rows,expand
    from calendar import monthrange
    days=Decimal(monthrange(month.year,month.month)[1])
    prefix=month.strftime('%Y-%m')
    for assignment in owner_rows(version):
        for key,(kind,label,rows) in expand(assignment).items():
            rows=[r for r in rows if r['date'].startswith(prefix)]
            if not rows:continue
            fields=[('Среднее количество · '+('чел.' if kind=='labor' else 'ед.'),'planned_count',0)] if kind!='fuel' else [('Расход · л','planned_liters',2)]
            comparisons=[]
            for title,field,_ in fields:
                value=sum((decimal(r.get(field)) for r in rows),ZERO)
                if field=='planned_count':value/=days
                comparisons.append(measure(title,value))
            grouped[kind][labor_group(rows[0].get('brigade_id')) if kind=='labor' else 'Назначения ресурсов'].append(SimpleNamespace(pk='assignment:'+key,label=label,kind=kind,path=label,native_prefix='',can_edit=False,
                metadata={'resource_assignment_id':assignment.pk,'target_label':rows[0].get('target_label','')},reference='',comparison=comparisons,errors=[],assignment_id=assignment.pk))
    sections=[{'kind':kind,'label':LABELS[kind],'groups':[{'label':name,'entries':entries} for name,entries in sorted(groups.items())],
        'totals':[],'count':sum(len(entries) for entries in groups.values())} for kind,groups in grouped.items()]
    for section in sections:
        if section['kind'] in {'labor','equipment'} and section['count']:
            total=sum((decimal(entry.comparison[0]['after']) for group in section['groups'] for entry in group['entries']),ZERO)
            section['totals']=[measure('Итого · '+('чел.' if section['kind']=='labor' else 'ед.'),total)]
    attach_work_hierarchy(sections)
    return {'sections':sections,'days':[]}


@transaction.atomic
def add_month_resource(user,version,month,data):
    from .preparation import require_resource_input
    version=GlobalPlanVersion.objects.select_for_update().get(pk=version.pk,company=user.company)
    require_resource_input(user,version);require_planning_object(user,version.construction_object_id)
    from .bulk_add import editable_months
    if month not in editable_months(version):raise ValidationError('Месяц недоступен для редактирования.')
    kind=data.get('kind')
    if kind not in {'labor','equipment','fuel'}:raise ValidationError('Выберите вид ресурса.')
    fields={'labor':{'brigade':data.get('brigade')},'equipment':{'equipment_type':data.get('equipment'),'equipment_number':''},'fuel':{'fuel_type':data.get('fuel'),'equipment_ref':''}}[kind]
    row,created=ResourceMonthAllocation.objects.get_or_create(company=user.company,version=version,month=month,kind=kind,defaults={'rate':data.get('rate') or 0},**fields)
    if created:
        version.snapshot={};version.save(update_fields=['snapshot'])
        from apps.audit.models import AuditLog
        AuditLog.objects.create(user=user,action='CREATE',entity_type='planning.resourcemonthallocation',entity_id=str(row.pk),new_value={'kind':kind,'month':month.isoformat()},comment='Добавлена строка ресурса через общий интерфейс планирования')
    if kind=='equipment':
        from .table_resource_crew import add_monthly_crew
        created=bool(add_monthly_crew(user,version,month,data['equipment'])) or created
    return created


@transaction.atomic
def remove_smg_entry(user,schedule,entry_id,revision):
    from .daily_schedule_table import locked
    from .preparation import require_resource_input
    schedule=locked(user,schedule)
    entry=DailyScheduleEntry.objects.select_for_update().get(pk=entry_id,schedule=schedule)
    if entry.kind=='work':require_roles(user,PLAN_ROLES)
    else:require_resource_input(user,schedule)
    if entry.revision!=revision:raise ValidationError('Строка уже изменена. Обновите страницу.')
    if schedule.locked_before:raise ValidationError('Строка содержит сохранённые закрытые дни. Удаление недоступно.')
    if entry.metadata.get('resource_assignment_id'):raise ValidationError('Измените назначение в интерфейсе связей.')
    if entry.kind=='work':
        from .resource_targets import identities
        work=entry.metadata.get('id')
        if any(w==work for a in schedule.resource_assignments.all() for w,i in identities(a)) or schedule.entries.filter(graph_links__work_id=work).exists():
            raise ValidationError('Сначала снимите связи этой работы с ресурсами в интерфейсе связей.')
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='DELETE',entity_type='planning.dailyscheduleentry',entity_id=str(entry.pk),old_value={'label':entry.label,'kind':entry.kind},comment='Удалена строка черновика СМГ')
    if entry.kind=='labor' and any(int(v) for v in entry.metadata.get('table_crew_counts',{}).values()):
        raise ValidationError('Должность входит в экипаж техники. Сначала удалите технику или измените её экипаж.')
    equipment=entry.kind=='equipment'
    entry.delete()
    if equipment:
        from .table_resource_crew import sync_daily
        sync_daily(user,schedule)
