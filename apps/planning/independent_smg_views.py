from datetime import date
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from core.permissions import READ_ROLES, PLAN_ROLES, require_roles, scope_queryset
from apps.projects.models import ConstructionObject
from .models import DailySchedule, GlobalPlanVersion
from . import independent_smg as service


class SMGForm(forms.Form):
    construction_object = forms.ModelChoiceField(label='Объект',queryset=ConstructionObject.objects.none())
    month = forms.DateField(label='Месяц',widget=forms.DateInput(attrs={'type':'month'},format='%Y-%m'),input_formats=['%Y-%m','%Y-%m-%d'])
    source = forms.ModelChoiceField(label='ГПР для сравнения (необязательно)',queryset=GlobalPlanVersion.objects.none(),required=False)
    copy_from_gpr = forms.BooleanField(label='Добавить данные из ГПР (для субобъекта — только состав работ)',required=False)

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['construction_object'].queryset = scope_queryset(ConstructionObject.objects.all(),user)
        from .subobject_smg import reference_choices
        self.fields['source'].queryset = reference_choices(user)
        self.fields['source'].label_from_instance = lambda v:f'{v.construction_object} · ГПР v{v.version_number} · {v.start_date:%m.%Y} — {v.end_date:%m.%Y}'

    def clean(self):
        data=super().clean()
        if data.get('construction_object') and data.get('source'):
            from .subobject_smg import valid_reference
            if not valid_reference(data['construction_object'],data['source']):self.add_error('source','Выберите ГПР этого объекта или его родительского объекта.')
        return data


class ReferenceForm(SMGForm):
    def __init__(self,*args,schedule,user,**kwargs):
        super().__init__(*args,user=user,**kwargs)
        self.fields.pop('construction_object');self.fields.pop('month');self.fields.pop('copy_from_gpr')
        from .subobject_smg import reference_choices
        self.fields['source'].queryset = reference_choices(user,schedule.construction_object).filter(start_date__lte=schedule.month,end_date__gte=schedule.month)
        self.fields['source'].initial = schedule.source_id


class SMGList(View):
    def get(self,request):
        require_roles(request.user,READ_ROLES)
        objects = scope_queryset(ConstructionObject.objects.all(),request.user)
        schedules = scope_queryset(DailySchedule.objects.all(),request.user).select_related('construction_object__parent','source__construction_object').defer('source__snapshot').order_by('-month','construction_object__name','-revision')
        obj=request.GET.get('object','');month=request.GET.get('month','');status=request.GET.get('status','')
        if obj.isdigit():schedules=schedules.filter(construction_object_id=obj)
        if month:
            try:schedules=schedules.filter(month=date.fromisoformat(month+'-01'))
            except ValueError:messages.error(request,'Проверьте месяц фильтра.')
        if status in dict(DailySchedule._meta.get_field('status').choices):schedules=schedules.filter(status=status)
        return render(request,'planning/independent_smg_list.html',{'schedules':schedules,'objects':objects,'selected_object':obj,'selected_month':month,'selected_status':status,'statuses':DailySchedule._meta.get_field('status').choices})


class SMGCreate(View):
    def display(self,request,bound=False):
        require_roles(request.user,PLAN_ROLES)
        from django.utils import timezone
        form=SMGForm(request.POST if bound else None,user=request.user,initial={'month':timezone.localdate().replace(day=1)})
        if bound and form.is_valid():
            try:
                schedule=service.create(request.user,form.cleaned_data['construction_object'],form.cleaned_data['month'],form.cleaned_data['source'],form.cleaned_data['copy_from_gpr'])
                return redirect('planning:daily_schedule_detail',pk=schedule.pk)
            except ValidationError as exc:form.add_error(None,exc)
        return render(request,'planning/independent_smg_create.html',{'form':form})
    def get(self,request):return self.display(request)
    def post(self,request):return self.display(request,True)


class SMGReference(View):
    def post(self,request,pk):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
        form=ReferenceForm(request.POST,schedule=schedule,user=request.user)
        if form.is_valid():
            try:
                service.reference(request.user,schedule,form.cleaned_data['source'])
                messages.success(request,'Ссылка на ГПР обновлена. Значения СМГ сохранены. Сравнение пересчитано.')
            except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
        else:messages.error(request,'Выберите доступный утвержденный ГПР этого объекта или его родителя.')
        return redirect('planning:daily_schedule_detail',pk=pk)


class SMGRevise(View):
    def post(self,request,pk):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
        try:return redirect('planning:daily_schedule_detail',pk=service.revise(request.user,schedule).pk)
        except ValidationError as exc:
            messages.error(request,'; '.join(exc.messages))
            return redirect('planning:daily_schedule_detail',pk=pk)
