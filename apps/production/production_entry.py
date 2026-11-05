"""Work candidates and employee assignments reused by the production dispatcher."""
from copy import copy
from django.db import transaction
from django import forms
from django.db.models import Max, Q
from apps.works.models import ProjectWork
from apps.resources.models import Position
from .dispatch_models import EmployeeActivity, EmployeeWorkLink
from .models import DailyFact
from .dispatch import PersonForm


GENERAL_PATH = 'employee__position__planning_mapping__brigade__group__is_general_staff'


@transaction.atomic
def inherit_parent_works(obj, user):
    """Materialise parent catalogue entries locally so facts remain owned by the child object."""
    from apps.projects.models import ConstructionObject,Section
    from apps.works.models import ComplexObject,ProjectWorkItem
    rows={row.pk:row for row in ConstructionObject.objects.filter(company=obj.company,project=obj.project)}
    ancestors=[];current=rows.get(obj.parent_id);seen=set()
    while current and current.pk not in seen:
        seen.add(current.pk);ancestors.append(current);current=rows.get(current.parent_id)
    if not ancestors:return
    ConstructionObject.objects.select_for_update().get(pk=obj.pk)
    local=list(ProjectWork.objects.filter(company=obj.company,section__construction_object=obj,merged_source__isnull=True).select_related('section','complex_object').prefetch_related('items'))
    signatures={(work.kind,work.section.name.casefold(),(work.complex_object.name.casefold() if work.complex_object_id else ''),work.name.casefold()) for work in local}
    # Nearest parent wins when the same catalogue path exists at several ancestor levels.
    ancestor_order={ancestor.pk:index for index,ancestor in enumerate(ancestors)}
    sources=list(ProjectWork.objects.filter(company=obj.company,section__construction_object__in=ancestors,merged_source__isnull=True,is_legacy_structure=False).select_related('section','complex_object').prefetch_related('items'))
    sources.sort(key=lambda work:(ancestor_order[work.section.construction_object_id],work.pk))
    for source in sources:
        signature=(source.kind,source.section.name.casefold(),(source.complex_object.name.casefold() if source.complex_object_id else ''),source.name.casefold())
        if signature in signatures:continue
        section=Section.objects.filter(company=obj.company,construction_object=obj,name__iexact=source.section.name).order_by('pk').first()
        if not section:section=Section.objects.create(company=obj.company,construction_object=obj,name=source.section.name)
        complex_object=None
        if source.complex_object_id:
            complex_object=ComplexObject.objects.filter(company=obj.company,construction_object=obj,name__iexact=source.complex_object.name).order_by('pk').first()
            if not complex_object:complex_object=ComplexObject.objects.create(company=obj.company,construction_object=obj,section=section,name=source.complex_object.name)
            section=complex_object.section
        work=copy(source);work.pk=None;work._state=copy(source._state);work._state.fields_cache={};work._state.adding=True;work.code='';work.section=section;work.complex_object=complex_object;work._price_actor=user;work.save()
        mapping={};pending=[]
        for original in source.items.all().order_by('sequence','pk'):
            item=copy(original);item.pk=None;item._state=copy(original._state);item._state.fields_cache={};item._state.adding=True;item.code='';item.project_work=work;item.parent=None;item.save()
            mapping[str(original.pk)]=item.pk;pending.append((item,original.parent_id))
        for item,parent_id in pending:
            if parent_id and str(parent_id) in mapping:item.parent_id=mapping[str(parent_id)];item.save(update_fields=['parent'])
        from apps.planning.models import SubobjectWorkOrigin
        link=SubobjectWorkOrigin(company=obj.company,source_work=source,local_work=work,item_mapping=mapping);link.full_clean();link.save()
        signatures.add(signature)


def is_general_employee(employee):
    mapping = getattr(employee.position, 'planning_mapping', None) if employee and employee.position_id else None
    group = mapping.brigade.group if mapping and mapping.brigade.group_id else None
    return bool(group and group.company_id == employee.company_id and group.is_general_staff)


def token(work, item=None):
    return f'item:{work}:{item}' if item else f'work:{work}'


def targets(activity):
    links = list(activity.work_links.all()) if activity.pk else []
    return [(link.work_id, link.work_item_id) for link in links] or ([(activity.work_id, activity.work_item_id)] if activity.work_id else [])


def work_catalogue(obj):
    choices = []
    works = ProjectWork.objects.filter(company=obj.company, section__construction_object=obj,
        merged_source__isnull=True).select_related('section','complex_object').prefetch_related('items').order_by('section__name','name','pk')
    for work in works:
        label = f'{work.section.name} → ' + (f'{work.complex_object.name} → ' if work.complex_object_id else '') + work.name
        if work.kind == 'SIMPLE':
            choices.append({'token':token(work.pk),'work':work.pk,'item':None,'label':label,'unit':work.unit,'title':work.name,'kind':'SIMPLE','object':work.complex_object.name if work.complex_object_id else work.section.name,'package':'','legacy':work.is_legacy_structure})
        else:
            for item in work.items.all():
                if item.company_id == obj.company_id:
                    legacy=work.is_legacy_structure or item.is_legacy_structure
                    choices.append({'token':token(work.pk,item.pk),'work':work.pk,'item':item.pk,'label':f'{label} → {item.name}','unit':item.unit,'title':item.name,'kind':'COMPOSITE','object':work.complex_object.name if work.complex_object_id else work.section.name,'package':work.name,'legacy':legacy})
    by_id = {work.pk:work for work in works}
    for choice in choices:
        work = by_id[choice['work']]
        choice.update(section_id=work.section_id,section=work.section.name,complex_id=work.complex_object_id,
            complex=work.complex_object.name if work.complex_object_id else '')
    return choices


def candidate_keys(obj, day, work_plan, catalogue, data=None, user=None, include_legacy=False):
    keys = set(work_plan)
    if include_legacy:keys.update((row['work'],row['item']) for row in catalogue if row.get('legacy'))
    facts=DailyFact.objects.filter(company=obj.company, project_work__section__construction_object=obj,date__lte=day)
    if user:facts=facts.filter(Q(date=day)|Q(reported_by=user)|Q(reported_by=None))
    keys.update(facts.values_list('project_work_id','work_item_id'))
    people = EmployeeActivity.objects.filter(company=obj.company, construction_object=obj, date__lte=day)
    if user:people=people.filter(Q(date=day)|Q(reported_by=user)|Q(reported_by=None))
    keys.update(people.exclude(work=None).values_list('work_id','work_item_id'))
    keys.update(EmployeeWorkLink.objects.filter(company=obj.company, activity__in=people).values_list('work_id','work_item_id'))
    if data is not None:
        for name in data:
            if name.startswith('works-') and name.endswith('-project_work'):
                prefix=name[:-len('project_work')]
                if data.get(prefix+'DELETE'):continue
                try:keys.add((int(data[name]),int(data[prefix+'work_item']) if data.get(prefix+'work_item') else None))
                except (ValueError,TypeError):pass
    allowed={(row['work'],row['item']) for row in catalogue}
    return keys & allowed


def previous_people(obj, day, *, production, user=None):
    qs = EmployeeActivity.objects.filter(company=obj.company, construction_object=obj, date__lt=day,
        employee__company=obj.company, employee__is_active=True)
    from .employee_pool import labor_direction
    qs = qs.exclude(labor_direction()) if production else qs.filter(labor_direction())
    if production and user:qs=qs.filter(Q(reported_by=user)|Q(reported_by=None))
    previous = qs.aggregate(day=Max('date'))['day']
    if previous is None:return None, []
    rows = qs.filter(date=previous).select_related('employee__position__planning_mapping__brigade__group').prefetch_related('work_links').order_by('employee__last_name','employee__first_name','employee_id','shift','pk')
    initial=[]
    from .production_positions import plan_positions
    current_roles=set(plan_positions(obj,day)) if production else set()
    for row in rows:
        saved_targets=targets(row)
        initial.append({'employee':row.employee_id,'role':row.planning_position_id or row.position_id,'shift':row.shift,'general':row.general,
            'work':row.work_id,'work_item':row.work_item_id,'equipment':row.equipment_id,'activity':row.activity,'planning_position':row.planning_position_id if row.planning_position_id in current_roles else None,
            'work_mode':('general' if row.general else 'works' if saved_targets else 'pending') if row.assignment_reviewed else 'pending',
            'work_targets':[token(w,i) for w,i in saved_targets]})
    return previous, initial


class ProductionPersonForm(PersonForm):
    production_entry = True
    shift = forms.TypedChoiceField(label='Смена', choices=[(1,'День'),(2,'Ночь')], coerce=int, initial=1,
        widget=forms.Select(attrs={'class':'form-select form-select-sm','data-compact-select':'true','aria-label':'Смена сотрудника'}))
    prepared_from_previous = False
    work_mode = forms.ChoiceField(label='Занятость', choices=[('pending','Связи пока не проверены'),('works','Выполнял работы'),('general','Работал без привязки к работе')],
        initial='pending', widget=forms.Select(attrs={'class':'form-select','data-native-select':'true'}))
    planning_position=forms.ModelChoiceField(label='Закрывает должность СМГ',queryset=Position.objects.none(),required=False,empty_label='По штатной должности сотрудника')
    class Meta(PersonForm.Meta):
        fields=[*PersonForm.Meta.fields,'planning_position']
    work_targets = forms.MultipleChoiceField(label='Работы сотрудника', required=False, widget=forms.MultipleHiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .production_positions import plan_positions
        from apps.resources.models import Position
        positions=set(self.planned_positions if self.planned_positions is not None else plan_positions(self.obj,self.day))
        if self.instance.pk and self.instance.planning_position_id:positions.add(self.instance.planning_position_id)
        self.fields['planning_position'].queryset=Position.objects.filter(company=self.obj.company,pk__in=positions).order_by('name')
        self.fields['planning_position'].help_text='Выберите должность плана, которую закрывает сотрудник. Штатная должность сотрудника не меняется.'
        from .employee_pool import prepare,apply
        pool=self.employee_pool or prepare(self.obj,self.day)
        apply(self.fields['employee'],pool,self.instance.employee if self.instance.employee_id else None)
        if '_position_choices' not in pool:
            pool['_position_choices']={p.pk:str(p) for p in Position.objects.filter(company=self.obj.company,pk__in=positions).order_by('name')}
        position_choices=dict(pool['_position_choices'])
        if self.instance.planning_position_id and self.instance.planning_position_id not in position_choices:position_choices[self.instance.planning_position_id]=str(self.instance.planning_position)
        self.fields['planning_position'].choices=[('',self.fields['planning_position'].empty_label),*position_choices.items()]
        if self.instance.pk:
            # Disabled saved selectors need one option, not a copy of the whole company.
            chosen=str(self.instance.employee_id)
            options=[(value,label) for _,group in pool['groups'] for value,label in group if str(value)==chosen]
            if not options:options=[(self.instance.employee_id,self.instance.employee.display_name)]
            self.fields['employee'].widget.choices=options
        self.fields['employee'].help_text='Все активные сотрудники. Сначала — ВЖГ этого объекта, затем другие ВЖГ и сотрудники без оформленного нахождения.'
        if self.instance.pk and self.instance.shift not in {1,2}:
            self.fields['shift'].choices=[*self.fields['shift'].choices,(self.instance.shift,f'Смена {self.instance.shift} (ранее)')]
        self.initial.setdefault('work_targets',[])
        self.initial.setdefault('work_mode','pending')
        self.fields['work_targets'].choices=[(row['token'],row['label']) for row in self.activity_choices if (row['work'],row['item']) in self.activity_work_keys]
        if self.instance.pk:
            self.initial['work_targets']=[token(w,i) for w,i in targets(self.instance)]
            self.initial['work_mode']=('general' if self.instance.general else 'works') if self.instance.assignment_reviewed else 'pending'
        self.independent_links=self.is_bound and self.data.get('collaborative')=='1'
        if self.independent_links:
            self.data=self.data.copy()
            selected=targets(self.instance) if self.instance.pk else []
            self.data.setlist(self.add_prefix('work_targets'),[token(w,i) for w,i in selected])
            self.data[self.add_prefix('work_mode')]=('general' if self.instance.general else 'works' if self.instance.assignment_reviewed and selected else 'pending') if self.instance.pk else 'pending'
        for name in ['work','work_item','general','equipment']:
            self.fields[name].widget=forms.HiddenInput()
        self.fields['activity'].label='Комментарий о работе'

    def has_changed(self):
        return self.prepared_from_previous or super().has_changed()

    def clean(self):
        data=super().clean()
        self.resolved_targets=None
        self.instance.assignment_reviewed=data.get('work_mode')!='pending'
        if data.get('work_mode')=='pending':
            data.update(general=False,work=None,work_item=None,work_targets=[])
            self.resolved_targets=[]
        elif data.get('work_mode')=='general':
            data.update(general=True,work=None,work_item=None,work_targets=[])
            self.resolved_targets=[]
        elif data.get('work_mode')=='works':
            selected=set(data.get('work_targets',[]))
            # Keep older clients posting the original single work fields compatible.
            if self.add_prefix('work_targets') not in self.data and data.get('work'):
                selected={token(data['work'].pk,data['work_item'].pk if data.get('work_item') else None)}
            chosen=[row for row in self.activity_choices if row['token'] in selected and (row['work'],row['item']) in self.activity_work_keys]
            if not chosen:
                self.add_error('work_targets','Выберите выполненные работы или вариант «Работал без привязки к работе».')
            else:
                first=chosen[0]
                data['work']=self.fields['work'].queryset.get(pk=first['work'])
                data['work_item']=self.fields['work_item'].queryset.get(pk=first['item']) if first['item'] else None
                data['general']=False
                self.resolved_targets=[(row['work'],row['item']) for row in chosen]
        return data


def sync_links(form):
    row=form.instance
    if getattr(form,'independent_links',False):return
    if not isinstance(row,EmployeeActivity) or not row.pk:return
    if hasattr(form,'resolved_targets'):
        selected=form.resolved_targets
        if selected is None:return
    elif row.general:
        selected=[]
    else:
        primary=(row.work_id,row.work_item_id) if row.work_id else None
        existing=targets(row)
        selected=existing if primary in existing and 'employee' not in form.changed_data else ([primary] if primary else [])
    current=set(row.work_links.values_list('work_id','work_item_id'))
    if current==set(selected):return
    row.work_links.all().delete()
    for work,item in selected:
        link=EmployeeWorkLink(company=row.company,activity=row,work_id=work,work_item_id=item)
        link.full_clean();link.save()


def report_data(obj,day):
    works=[]
    for row in DailyFact.objects.filter(company=obj.company,project_work__section__construction_object=obj,date=day).select_related('reported_by'):
        works.append({**{name:getattr(row,name) for name in ('id','project_work_id','work_item_id','actual_quantity','actuation_type','actuation_reason','actuation_reason_ids','comment')},
            'reported_by__username':row.reported_by.username if row.reported_by_id else None,
            'owner':(row.reported_by.get_full_name() or row.reported_by.username) if row.reported_by_id else 'Общий ввод'})
    people=[]
    for row in EmployeeActivity.objects.filter(company=obj.company,construction_object=obj,date=day).select_related(
            'employee__position__planning_mapping__brigade__group','position__planning_mapping__brigade__group','planning_position__planning_mapping__brigade__group','reported_by').prefetch_related('work_links'):
        role=row.planning_position or row.position
        mapping=getattr(role,'planning_mapping',None) if role else None
        group=mapping.brigade.group if mapping and mapping.brigade.group_id else None
        from apps.resources.group_order import labor_group_order
        people.append({'group_order':labor_group_order(group)[0],'group':group.name if group else 'Без группы плана','id':row.pk,'employee':row.employee_id,'name':row.employee.display_name,'position':str(row.planning_position or row.position or ''),'staff_position':str(row.position or ''),
            'role':row.planning_position_id or row.position_id,'shift':row.shift,'general':row.general,'reviewed':row.assignment_reviewed,'constant':is_general_employee(row.employee),
            'links_revision':row.links_revision,'direction':row.entry_direction,'targets':[token(w,i) for w,i in targets(row)],
            'owner':(row.reported_by.get_full_name() or row.reported_by.username) if row.reported_by_id else 'Общий ввод'})
    return {'works':works,'people':people}
