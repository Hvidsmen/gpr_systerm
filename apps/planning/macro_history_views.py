from datetime import date
from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.views import View
from core.permissions import require_roles,PLAN_ROLES,READ_ROLES
from apps.projects.models import ConstructionObject
from .models import PlanningWorkspace,GlobalPlanVersion
from .macro_history import create_macro,history_links


class MacroForm(forms.Form):
    macro=forms.ModelChoiceField(queryset=ConstructionObject.objects.none(),label='Макрообъект (например, КТП)')
    sources=forms.ModelMultipleChoiceField(queryset=GlobalPlanVersion.objects.none(),label='Утвержденные ГПР субобъектов',widget=forms.SelectMultiple(attrs={'size':8}))
    name=forms.CharField(label='Название общего ГПР',max_length=255)
    start=forms.DateField(label='История с',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    new_start=forms.DateField(label='Новый период с',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    end=forms.DateField(label='Общий план до',widget=forms.DateInput(attrs={'type':'date'},format='%Y-%m-%d'))
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['macro'].queryset=ConstructionObject.objects.filter(company=user.company).select_related('project')
        self.fields['sources'].queryset=GlobalPlanVersion.objects.filter(company=user.company,status__in=['APPROVED','COMPLETED'],workspace__parent_gpr__isnull=True).select_related('construction_object').defer('snapshot').order_by('construction_object__name','-pk')
        self.fields['sources'].label_from_instance=lambda v:f'{v.construction_object} · v{v.version_number} · {v.start_date:%m.%Y}–{v.end_date:%m.%Y}'
        self.fields['sources'].help_text='Выберите одну версию на каждый субобъект (Ctrl для нескольких). Объекты без родителя будут привязаны к выбранному макрообъекту; объекты другого проекта или другого родителя не допускаются.'


class MacroCreate(View):
    def dispatch(self,request,*args,**kwargs):
        require_roles(request.user,PLAN_ROLES);return super().dispatch(request,*args,**kwargs)
    def get(self,request):
        return render(request,'planning/macro_create.html',{'form':MacroForm(user=request.user)})
    def post(self,request):
        form=MacroForm(request.POST,user=request.user)
        if form.is_valid():
            d=form.cleaned_data
            try:
                workspace=create_macro(request.user,d['macro'],list(d['sources']),d['name'],d['start'],d['new_start'],d['end'])
                return redirect('planning:macro_history_detail',pk=workspace.pk)
            except ValidationError as e:form.add_error(None,e)
        return render(request,'planning/macro_create.html',{'form':form})


class MacroDetail(View):
    def get(self,request,pk):
        require_roles(request.user,READ_ROLES)
        w=get_object_or_404(PlanningWorkspace.objects.select_related('construction_object','baseline_version').defer('baseline_version__snapshot'),pk=pk,company=request.user.company,is_macro_consolidation=True)
        version=w.versions.defer('snapshot').order_by('-version_number').first()
        target_month=version.planning_month or w.continuation_start
        month=target_month.isoformat()
        return render(request,'planning/macro_history_detail.html',{'workspace':w,'version':version,'history':history_links(w),'month':month,'target_month':target_month,'report_url':reverse('planning:report_matrix')+f'?objects={w.construction_object_id}&start={w.start_date.isoformat()}&end={w.end_date.isoformat()}'})
