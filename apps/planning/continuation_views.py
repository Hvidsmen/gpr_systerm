from calendar import monthrange
from datetime import timedelta
from django import forms
from django.core.exceptions import ValidationError
from django.shortcuts import render,redirect,get_object_or_404
from django.views import View
from core.permissions import require_roles,PLAN_ROLES
from .models import GlobalPlanVersion
from .continuation import continue_gpr


class ContinuationForm(forms.Form):
    source=forms.ModelChoiceField(label='Предыдущий утвержденный ГПР',queryset=GlobalPlanVersion.objects.none())
    name=forms.CharField(label='Название объединенного плана',max_length=255)
    end_date=forms.DateField(label='Конец нового периода',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['source'].queryset=GlobalPlanVersion.objects.filter(company=user.company,status__in=['APPROVED','COMPLETED'],workspace__parent_gpr__isnull=True).select_related('construction_object').defer('snapshot').order_by('-end_date','-pk')
        from core.permissions import scope_queryset
        self.fields['source'].queryset=scope_queryset(self.fields['source'].queryset,user)
        self.fields['source'].label_from_instance=lambda v:f'{v.construction_object} · {v.start_date:%m.%Y}–{v.end_date:%m.%Y} · v{v.version_number}'


class ContinueGpr(View):
    def dispatch(self,request,*args,**kwargs):
        require_roles(request.user,PLAN_ROLES)
        return super().dispatch(request,*args,**kwargs)
    def get(self,request):
        initial={}
        if request.GET.get('source'):
            source=get_object_or_404(GlobalPlanVersion.objects.defer('snapshot'),pk=request.GET['source'],company=request.user.company,status__in=['APPROVED','COMPLETED'])
            start=source.end_date+timedelta(days=1)
            initial={'source':source.pk,'name':source.title or str(source.construction_object),'end_date':start.replace(day=monthrange(start.year,start.month)[1])}
        return render(request,'planning/continue_gpr.html',{'form':ContinuationForm(user=request.user,initial=initial)})
    def post(self,request):
        form=ContinuationForm(request.POST,user=request.user)
        if form.is_valid():
            try:
                w=continue_gpr(request.user,form.cleaned_data['source'],form.cleaned_data['name'],form.cleaned_data['end_date'])
                return redirect('planning:workspace_edit',pk=w.baseline_version_id)
            except ValidationError as e:form.add_error(None,e)
        return render(request,'planning/continue_gpr.html',{'form':form})
