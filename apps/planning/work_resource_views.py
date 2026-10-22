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
    refresh_crew=forms.BooleanField(label='Обновить экипаж из справочника техники',required=False)
    class Meta:
        model=WorkResourceAssignment
        fields=['work','work_item','equipment_type','position','start_date','end_date','count','shifts_per_day','hours_per_shift']
        widgets={name:forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d') for name in ['start_date','end_date']}

    def __init__(self,*args,owner,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=owner.company
        self.instance.version=owner if isinstance(owner,GlobalPlanVersion) else None
        self.instance.schedule=owner if isinstance(owner,DailySchedule) else None
        self.fields['work'].queryset=ProjectWork.objects.filter(company=owner.company,section__construction_object_id=owner.construction_object_id).filter(Q(merged_source__isnull=True)|Q(pk=self.instance.work_id))
        self.fields['work'].empty_label='Общие ресурсы строительного объекта'
        self.fields['work_item'].queryset=ProjectWorkItem.objects.filter(company=owner.company,project_work__in=self.fields['work'].queryset)
        self.fields['work_item'].label_from_instance=lambda i:f'{i.project_work.name} → {i.name}'
        self.fields['equipment_type'].queryset=EquipmentType.objects.filter(company=owner.company).filter(Q(merge_source__isnull=True,is_active=True)|Q(pk=self.instance.equipment_type_id))
        self.fields['position'].queryset=Position.objects.filter(company=owner.company)
        self.fields['work_item'].help_text='Необязательно. Должна принадлежать выбранному комплексу работ.'
        self.fields['position'].help_text='Для людей без привязки к технике. При выборе техники должности берутся из её экипажа.'
        for name in ['count','shifts_per_day','hours_per_shift']:self.fields[name].widget.attrs['min']='1' if name!='hours_per_shift' else '0.01'


class WorkResources(View):
    kind=None
    def display(self,request,pk,assignment_pk=None,bound=False):
        require_roles(request.user,PLAN_ROLES if bound else READ_ROLES)
        model=DailySchedule if self.kind=='smg' else GlobalPlanVersion
        owner=get_object_or_404(scope_queryset(model.objects.all(),request.user),pk=pk)
        instance=get_object_or_404(owner.resource_assignments,pk=assignment_pk) if assignment_pk else WorkResourceAssignment()
        from .estimate_review import review_period
        start,end=review_period(owner)
        if isinstance(owner,DailySchedule) and owner.locked_before:start=max(start,owner.locked_before)
        form=AssignmentForm(request.POST if bound else None,owner=owner,instance=instance,initial={'start_date':start,'end_date':end} if not instance.pk else {})
        if instance.pk and isinstance(owner,DailySchedule) and owner.locked_before and instance.start_date<owner.locked_before:
            for name in ['start_date','work','work_item','equipment_type','position']:form.fields[name].disabled=True
        can_edit=role_code(request.user) in PLAN_ROLES and owner.status in ['DRAFT','REJECTED']
        if bound:
            try:
                if request.POST.get('action')=='delete' and assignment_pk:
                    service.remove(request.user,owner,instance)
                    messages.success(request,'Назначение снято. Закрытые дни, если они были, сохранены.')
                    return redirect('planning:work_resources_'+self.kind,pk=pk)
                if form.is_valid():
                    service.save(request.user,owner,form.instance,form.cleaned_data['refresh_crew'])
                    messages.success(request,'Назначение сохранено. Потребность в экипаже рассчитана на каждую смену.')
                    return redirect('planning:work_resources_'+self.kind,pk=pk)
            except ValidationError as exc:form.add_error(None,exc)
        rows=list(service.owner_rows(owner));totals={}
        generated=[]
        for row in rows:
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
