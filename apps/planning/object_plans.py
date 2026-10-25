"""Independent work planning and explicit links to GPR / SMG."""
from decimal import Decimal,InvalidOperation
from django import forms
from django.contrib import messages
from django.db import transaction
from django.core.exceptions import ValidationError
from django.forms import modelformset_factory,BaseModelFormSet
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.views import View
from core.permissions import PLAN_ROLES,READ_ROLES,require_roles,scope_queryset,require_planning_object,role_code
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from .models import ObjectWorkPlan,ObjectWorkPlanRow,GlobalPlanVersion,DailySchedule
from .work_selection import choices,resolve


def works_for(plan,user):
    return scope_queryset(ProjectWork.objects.filter(section__construction_object_id=plan.construction_object_id,merged_source__isnull=True),user)


class ObjectPlanForm(forms.ModelForm):
    class Meta:
        model=ObjectWorkPlan
        fields=['construction_object','name','description','gpr_versions','smg_schedules']
        widgets={'description':forms.Textarea(attrs={'rows':2})}

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=user.company
        self.fields['construction_object'].queryset=scope_queryset(ConstructionObject.objects.all(),user)
        if self.instance.pk:self.fields['construction_object'].disabled=True
        object_id=self.instance.construction_object_id or (self.data.get('construction_object') if self.is_bound else self.initial.get('construction_object'))
        if not str(object_id or '').isdigit():object_id=None
        self.fields['gpr_versions'].queryset=scope_queryset(GlobalPlanVersion.objects.filter(construction_object_id=object_id),user).select_related('construction_object').defer('snapshot')
        self.fields['smg_schedules'].queryset=scope_queryset(DailySchedule.objects.filter(construction_object_id=object_id),user).select_related('construction_object').defer('source__snapshot')
        self.fields['gpr_versions'].label_from_instance=lambda v:f'{v.title or "ГПР"} · v{v.version_number} · {v.start_date:%d.%m.%Y} — {v.end_date:%d.%m.%Y}'
        self.fields['smg_schedules'].label_from_instance=lambda s:f'СМГ {s.month:%m.%Y} · ред. {s.revision} · {s.get_status_display()}'
        self.fields['gpr_versions'].help_text='Ссылки на планы этого объекта. Данные планов сохраняются независимо.'
        self.fields['smg_schedules'].help_text='Связать СМГ можно и без связанного ГПР.'


class BranchForm(forms.Form):
    selection=forms.ChoiceField(label='Добавить из справочника')
    quantity=forms.DecimalField(label='Количество выбранной позиции',min_value=Decimal('0'),max_digits=16,decimal_places=6,initial=1)
    def __init__(self,*args,plan,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['selection'].choices=choices(works_for(plan,user))
        self.fields['quantity'].help_text='Для комплекса — количество комплексов; объёмы его работ рассчитываются по нормативам. Уже добавленные объёмы сохраняются.'


class RowSet(BaseModelFormSet):
    def add_fields(self,form,index):
        super().add_fields(form,index)
        form.fields['id'].queryset=self.get_queryset()


Rows=modelformset_factory(ObjectWorkPlanRow,fields=['quantity'],formset=RowSet,extra=0,can_delete=True,max_num=10000,validate_max=True)


def audit(user,plan,action,comment):
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action=action,entity_type='planning.ObjectWorkPlan',entity_id=str(plan.pk),comment=comment)


@transaction.atomic
def add_branch(user,plan,selection,quantity):
    require_roles(user,PLAN_ROLES)
    plan=get_object_or_404(scope_queryset(ObjectWorkPlan.objects.select_for_update(),user),pk=plan.pk)
    require_planning_object(user,plan.construction_object_id)
    if quantity<0:raise ValidationError('Объём не может быть отрицательным.')
    resolved=resolve(works_for(plan,user),selection)
    count=0
    for work,selected in resolved:
        items=[i for i in work.items.all() if i.company_id==plan.company_id and (not selected or i.pk in selected)]
        additions=[]
        if not selected:additions.append((None,quantity))
        selected_root=next((i for i in items if f'item:{i.pk}'==selection),None)
        if selected_root and selected_root.quantity_per_unit<=0:raise ValidationError('У выбранной работы некорректный норматив.')
        for item in items:
            volume=quantity*item.quantity_per_unit
            if selected_root:
                volume=quantity if item.pk==selected_root.pk else quantity*item.quantity_per_unit/selected_root.quantity_per_unit
            try:volume=volume.quantize(Decimal('.000001'))
            except InvalidOperation:raise ValidationError('Слишком большой объём работы.')
            additions.append((item,volume))
        for item,volume in additions:
            row,created=plan.rows.get_or_create(company=plan.company,work=work,work_item=item,defaults={'quantity':volume})
            if created:row.full_clean();count+=1
    plan.save(update_fields=['updated_at'])
    audit(user,plan,'UPDATE','Добавлено строк из справочника: '+str(count))
    return count


def work_groups(rows):
    sections={};invalid=[]
    for form in rows:
        if not form.instance.work_id:invalid.append(form);continue
        work=form.instance.work
        form.fields['quantity'].widget.attrs['aria-label']='Плановый объём: '+form.instance.label
        section=sections.setdefault(work.section_id,{'name':work.section.name,'groups':{}})
        group=section['groups'].setdefault(work.complex_object_id,{'object':work.complex_object,'packages':{}})
        package=group['packages'].setdefault(work.pk,{'work':work,'parent':None,'items':[]})
        if form.instance.work_item_id:package['items'].append(form)
        else:package['parent']=form
    for section in sections.values():
        for group in section['groups'].values():group['packages']=list(group['packages'].values())
        section['groups']=list(section['groups'].values())
    return list(sections.values()),invalid


class ObjectPlanList(View):
    def get(self,request):
        require_roles(request.user,READ_ROLES)
        plans=scope_queryset(ObjectWorkPlan.objects.all(),request.user).select_related('construction_object').prefetch_related('gpr_versions','smg_schedules')
        object_id=request.GET.get('object','')
        if object_id.isdigit():plans=plans.filter(construction_object_id=object_id)
        term=request.GET.get('q','').strip()
        if term:plans=plans.filter(name__icontains=term)
        return render(request,'planning/object_plan_list.html',{'object_plan_tab':True,'plans':plans,'objects':scope_queryset(ConstructionObject.objects.all(),request.user),'selected_object':object_id,'query':term})


class ObjectPlanCreate(View):
    def display(self,request,bound=False):
        require_roles(request.user,PLAN_ROLES)
        form=ObjectPlanForm(request.POST if bound else None,user=request.user,instance=ObjectWorkPlan(company=request.user.company,created_by=request.user),initial={'construction_object':request.GET.get('object')})
        if bound and form.is_valid():
            with transaction.atomic():
                require_planning_object(request.user,form.cleaned_data['construction_object'].pk)
                plan=form.save();audit(request.user,plan,'CREATE','Создан независимый план работ на весь объект')
            return redirect('planning:object_plan_detail',pk=plan.pk)
        return render(request,'planning/object_plan_create.html',{'object_plan_tab':True,'form':form},status=400 if bound else 200)
    def get(self,request):return self.display(request)
    def post(self,request):return self.display(request,True)


class ObjectPlanDetail(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,PLAN_ROLES if bound else READ_ROLES)
        plan=get_object_or_404(scope_queryset(ObjectWorkPlan.objects.all(),request.user),pk=pk)
        action=request.POST.get('action') if bound else ''
        form=ObjectPlanForm(request.POST if action=='settings' else None,user=request.user,instance=plan)
        rows=Rows(request.POST if action=='rows' else None,prefix='rows',queryset=plan.rows.select_related('work__section','work__complex_object','work_item'))
        branch=BranchForm(request.POST if action=='add' else None,plan=plan,user=request.user)
        success=False
        if bound:
            try:
                with transaction.atomic():
                    ObjectWorkPlan.objects.select_for_update().get(pk=plan.pk,company=request.user.company)
                    require_planning_object(request.user,plan.construction_object_id)
                    if action=='settings' and form.is_valid():form.save();audit(request.user,plan,'UPDATE','Изменены настройки и связи плана');success=True
                    elif action=='rows' and rows.is_valid():rows.save();plan.save(update_fields=['updated_at']);audit(request.user,plan,'UPDATE','Сохранены объёмы работ плана');success=True
                    elif action=='add' and branch.is_valid():
                        count=add_branch(request.user,plan,branch.cleaned_data['selection'],branch.cleaned_data['quantity'])
                        messages.success(request,f'Добавлено строк: {count}. Ранее заданные объёмы сохранены.');success=True
                    elif action not in ['settings','rows','add']:raise ValidationError('Неизвестное действие.')
                if success:
                    messages.success(request,'План сохранён.')
                    return redirect('planning:object_plan_detail',pk=plan.pk)
            except ValidationError as error:
                for message in error.messages:messages.error(request,message)
        sections,invalid=work_groups(rows)
        return render(request,'planning/object_plan_detail.html',{'object_plan_tab':True,'plan':plan,'form':form,'rows':rows,'work_sections':sections,'invalid_work_rows':invalid,'branch_form':branch,'editable':role_code(request.user) in PLAN_ROLES},status=400 if bound else 200)
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


def linked_plans(owner,user):
    lookup={'smg_schedules':owner} if isinstance(owner,DailySchedule) else {'gpr_versions':owner}
    return scope_queryset(ObjectWorkPlan.objects.filter(**lookup),user).filter(construction_object_id=owner.construction_object_id)
