from django import forms
from django.db.models import Q
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from core.permissions import scope_queryset, PLAN_ROLES, READ_ROLES, require_roles, role_code
from apps.works.models import ProjectWork, ProjectWorkItem
from apps.resources.models import EquipmentType, Position
from .models import WorkResourceAssignment, GlobalPlanVersion, DailySchedule
from . import work_resources as service


class AssignmentForm(forms.ModelForm):
    target_scope = forms.ChoiceField(label='Привязка ресурса', required=False,
        choices=[('works','К выбранным работам'),('object','Общие ресурсы объекта')],
        widget=forms.Select(attrs={'class':'form-select','data-native-select':'true'}))
    targets = forms.MultipleChoiceField(label='Работы', required=False,
        widget=forms.CheckboxSelectMultiple)

    refresh_crew=forms.BooleanField(label='Обновить экипаж из справочника техники',required=False)
    class Meta:
        model=WorkResourceAssignment
        fields=['work','work_item','equipment_type','position','start_date','end_date','count','shifts_per_day','hours_per_shift','fuel_type','daily_liters','fuel_price','include_crew']
        widgets={name:forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d') for name in ['start_date','end_date']}

    def __init__(self,*args,owner,**kwargs):
        super().__init__(*args,**kwargs)
        self.owner = owner
        self.instance.company=owner.company
        self.instance.version=owner if isinstance(owner,GlobalPlanVersion) else None
        self.instance.schedule=owner if isinstance(owner,DailySchedule) else None
        self.fields['work'].queryset=ProjectWork.objects.filter(company=owner.company,section__construction_object_id=owner.construction_object_id).filter(Q(merged_source__isnull=True)|Q(pk__in=[self.instance.work_id, *self.instance.extra_targets.values_list('work_id',flat=True)] if self.instance.pk else []))
        self.fields['work'].empty_label='Общие ресурсы строительного объекта'
        self.fields['work_item'].queryset=ProjectWorkItem.objects.filter(company=owner.company,project_work__in=self.fields['work'].queryset)
        self.fields['work_item'].label_from_instance=lambda i:f'{i.project_work.name} → {i.name}'
        self.fields['equipment_type'].queryset=EquipmentType.objects.filter(company=owner.company).filter(Q(merge_source__isnull=True,is_active=True)|Q(pk=self.instance.equipment_type_id))
        from apps.resources.equipment_levels import gpr_types
        field=self.fields['equipment_type'];allowed=gpr_types(field.queryset)
        field.label='Техника для планирования'
        field.queryset=field.queryset.filter(Q(pk__in=allowed.values('pk'))|Q(pk=self.instance.equipment_type_id))
        from apps.resources.position_catalog import planning_positions
        allowed_positions = planning_positions(owner.company)
        current_position_id = self.instance.position_id if self.instance.pk else None
        self.fields['position'].queryset=Position.objects.filter(company=owner.company).filter(
            Q(pk__in=allowed_positions.values('pk')) | Q(pk=current_position_id)).select_related('planning_mapping__brigade')
        self.fields['position'].label_from_instance=lambda position: position.planning_mapping.brigade.name if getattr(position,'planning_mapping',None) else position.name
        self.fields['work_item'].help_text='Необязательно. Должна принадлежать выбранному комплексу работ.'
        self.fields['position'].help_text='Только должности с активными сотрудниками. При выборе техники должности берутся из её экипажа.'
        for name in ['count','shifts_per_day','hours_per_shift']:self.fields[name].widget.attrs['min']='1' if name!='hours_per_shift' else '0.01'
        for name in ['work','work_item']:self.fields[name].widget = forms.HiddenInput()
        from .resource_targets import identities
        self.initial.setdefault('target_scope','works' if self.instance.work_id else 'object')
        self.initial.setdefault('targets',[f'item:{w}:{i}' if i else f'work:{w}' for w,i in identities(self.instance) if i or not (self.instance.work_id==w and self.instance.work.kind=='COMPOSITE')])
        self.configure_targets()

    def configure_targets(self):
        works = list(self.fields['work'].queryset.select_related('complex_object').order_by('name','pk'))
        by_id = {w.pk:w for w in works}
        choices = []
        def label(work):return f'{work.complex_object.name} → {work.name}' if work.complex_object_id else work.name
        for work in works:
            if work.kind != 'COMPOSITE':choices.append((f'work:{work.pk}',label(work)))
        for item in self.fields['work_item'].queryset.filter(project_work_id__in=by_id).order_by('project_work_id','sequence','pk'):
            choices.append((f'item:{item.project_work_id}:{item.pk}',f'{label(by_id[item.project_work_id])} → {item.name}'))
        self.fields['targets'].choices = choices

    def clean(self):
        data = super().clean()
        self.resolved_targets = None
        # Older callers still post the original pair of work fields.
        if not self.is_bound or 'target_scope' not in self.data or self.fields['targets'].disabled:return data
        scope = data.get('target_scope')
        if (data.get('fuel_type') or data.get('resource_kind')=='fuel') and scope!='object':
            self.add_error('target_scope','ГСМ назначается только на объект.')
        if scope == 'object':
            if data.get('targets'):self.add_error('targets','Общие ресурсы объекта не совмещаются с выбором работ.')
            self.resolved_targets = []
        elif scope == 'works':
            selected = data.get('targets',[])
            if not selected:self.add_error('targets','Выберите хотя бы одну работу.')
            else:
                pairs = []
                for token in selected:
                    parts = token.split(':')
                    pairs.append((parts[1], parts[2] if parts[0]=='item' else None))
                try:
                    from .resource_targets import normalize
                    self.resolved_targets = normalize(self.owner,pairs)
                except ValidationError as exc:self.add_error('targets',exc)
        else:self.add_error('target_scope','Выберите привязку ресурса.')
        if self.resolved_targets is not None:
            work_id,item_id = self.resolved_targets[0] if self.resolved_targets else (None,None)
            data['work'] = self.fields['work'].queryset.filter(pk=work_id).first() if work_id else None
            data['work_item'] = self.fields['work_item'].queryset.filter(pk=item_id).first() if item_id else None
        return data


class WorkResources(View):
    kind=None
    def display(self,request,pk,assignment_pk=None,bound=False):
        require_roles(request.user,{'ADMIN','PLANNER','MANAGER'} if bound else READ_ROLES)
        model=DailySchedule if self.kind=='smg' else GlobalPlanVersion
        owner=get_object_or_404(scope_queryset(model.objects.all(),request.user),pk=pk)
        instance=get_object_or_404(owner.resource_assignments,pk=assignment_pk) if assignment_pk else WorkResourceAssignment()
        from .estimate_review import review_period
        if self.kind=='gpr' and not bound:
            from django.urls import reverse
            from urllib.parse import urlencode
            if owner.workspace_id and owner.status in ['DRAFT','REJECTED'] and role_code(request.user) in PLAN_ROLES:
                month=owner.planning_month or (instance.start_date.replace(day=1) if instance.pk else owner.workspace.continuation_start or owner.start_date)
                params={'month':month.isoformat()}
                if instance.work_id:params['focus_work']=instance.work_id
                return redirect(reverse('planning:workspace_edit',args=[owner.pk])+'?'+urlencode(params))
            return redirect(reverse('planning:global_detail',args=[owner.pk])+'#work-resources')
        start,end=review_period(owner)
        if isinstance(owner,DailySchedule) and owner.locked_before:start=max(start,owner.locked_before)
        form=AssignmentForm(request.POST if bound else None,owner=owner,instance=instance,initial={'start_date':start,'end_date':end} if not instance.pk else {})
        if instance.pk and isinstance(owner,DailySchedule) and owner.locked_before and instance.start_date<owner.locked_before:
            for name in ['start_date','work','work_item','equipment_type','position','target_scope','targets']:form.fields[name].disabled=True
        from .preparation import record
        preparation=record(owner)
        can_edit=(role_code(request.user)=='ADMIN' or role_code(request.user)=='MANAGER' and preparation and preparation.manager_id==request.user.pk) and owner.status in ['DRAFT','REJECTED']
        if bound:
            try:
                if request.POST.get('action')=='delete' and assignment_pk:
                    service.remove(request.user,owner,instance)
                    messages.success(request,'Назначение снято. Закрытые дни, если они были, сохранены.')
                    return redirect('planning:work_resources_'+self.kind,pk=pk)
                if form.is_valid():
                    service.save(request.user,owner,form.instance,form.cleaned_data['refresh_crew'],targets=form.resolved_targets)
                    messages.success(request,'Назначение сохранено. Потребность в экипаже рассчитана на каждую смену.')
                    return redirect('planning:work_resources_'+self.kind,pk=pk)
            except ValidationError as exc:form.add_error(None,exc)
        rows=list(service.owner_rows(owner));totals={}
        generated=[]
        from .resource_targets import describe
        for row in rows:
            row.target_labels = describe(row)
            row.shared = len(row.target_labels)>1
            row.members=[dict(member,per_shift=row.count*member['count'],per_day=row.count*row.shifts_per_day*member['count']) for member in row.crew_snapshot]
            if not isinstance(owner,DailySchedule):generated.extend(service.expand(row).values())
        if isinstance(owner,DailySchedule):generated=[(entry.kind,entry.label,entry.rows) for entry in owner.entries.filter(key__startswith='assignment:')]
        for kind,label,values in generated:
            if kind!='labor':continue
            for value in values:
                index=(value['date'],value['position_id'])
                total=totals.setdefault(index,{'date':value['date'],'name':value.get('label',label),'count':0,'hours':0})
                from decimal import Decimal
                total['count']+=int(Decimal(value['planned_count']));total['hours']+=Decimal(value['planned_hours'])
        return render(request,'planning/work_resources.html',{'owner':owner,'kind':self.kind,'assignments':rows,'form':form,'can_edit':can_edit,'editing':assignment_pk,'totals':sorted(totals.values(),key=lambda r:(r['date'],r['name']))})
    def get(self,request,pk,assignment_pk=None):return self.display(request,pk,assignment_pk)
    def post(self,request,pk,assignment_pk=None):return self.display(request,pk,assignment_pk,True)
