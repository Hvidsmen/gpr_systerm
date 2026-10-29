from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from core.permissions import PLAN_ROLES, READ_ROLES, require_roles, scope_queryset
from .models import DailySchedule
from . import independent_smg, smg_comparison


class ComparisonForm(forms.Form):
    other = forms.ModelChoiceField(label='Сравнить с вариантом', queryset=DailySchedule.objects.none())
    differences = forms.BooleanField(label='Только отличия', required=False)

    def __init__(self, *args, user, schedule, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['other'].queryset = scope_queryset(DailySchedule.objects.filter(construction_object_id=schedule.construction_object_id,
            month=schedule.month), user).exclude(pk=schedule.pk).order_by('-revision')
        self.fields['other'].label_from_instance = lambda s: f'{s.name or "Вариант " + str(s.revision)} · №{s.revision} · {s.get_status_display()}'


class SMGCompare(View):
    def get(self, request, pk):
        require_roles(request.user, READ_ROLES)
        schedule = get_object_or_404(scope_queryset(DailySchedule.objects.all(), request.user), pk=pk)
        data = request.GET.copy()
        if not data:
            target = scope_queryset(DailySchedule.objects.filter(construction_object_id=schedule.construction_object_id, month=schedule.month), request.user).exclude(pk=pk).order_by('-revision').first()
            data['other'] = str(target.pk) if target else ''
            data['differences'] = 'on'
        form = ComparisonForm(data, user=request.user, schedule=schedule)
        sections, other, error = [], None, ''
        if form.is_valid():
            other = form.cleaned_data['other']
            try:
                sections = smg_comparison.compare(schedule, other, form.cleaned_data['differences'])
            except ValidationError as exc:
                error = '; '.join(exc.messages)
        return render(request, 'planning/smg_compare.html', {'schedule': schedule, 'form': form, 'other': other, 'sections': sections, 'error': error})


class CopyVariantForm(forms.Form):
    month=forms.DateField(label='Месяц копии',widget=forms.DateInput(attrs={'type':'month'},format='%Y-%m'),input_formats=['%Y-%m','%Y-%m-%d'])
    name=forms.CharField(label='Название копии',max_length=150,required=False)

    def clean_month(self):
        month=self.cleaned_data['month']
        if month.day!=1:raise ValidationError('Выберите первый день месяца.')
        return month


class SMGCopyVariant(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
        form=CopyVariantForm(request.POST if bound else None,initial={'month':schedule.month,'name':((schedule.name+' — копия')[:150] if schedule.name else '')})
        if bound and form.is_valid():
            try:
                from .smg_custom_copy import copy_variant
                new=copy_variant(request.user,schedule,form.cleaned_data['month'],form.cleaned_data['name'])
                messages.success(request,'Создан независимый черновик на выбранный месяц. Исходный вариант сохранён.')
                return redirect('planning:daily_schedule_detail',pk=new.pk)
            except ValidationError as exc:form.add_error(None,exc)
        return render(request,'planning/smg_copy_variant.html',{'form':form,'schedule':schedule})
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


class RenameForm(forms.Form):
    name=forms.CharField(label='Название СМГ',max_length=150,required=False)
    version=forms.CharField(widget=forms.HiddenInput)


class SMGRename(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,PLAN_ROLES)
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
        form=RenameForm(request.POST if bound else None,initial={'name':schedule.name,'version':schedule.updated_at.isoformat()})
        if bound and form.is_valid():
            try:
                from .smg_custom_copy import rename
                rename(request.user,schedule,form.cleaned_data['name'],form.cleaned_data['version'])
                messages.success(request,'Название СМГ сохранено.')
                return redirect('planning:daily_schedule_list')
            except ValidationError as exc:form.add_error(None,exc)
        return render(request,'planning/smg_rename.html',{'form':form,'schedule':schedule})
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


class CancelForm(forms.Form):
    version = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Причина отмены утверждения',max_length=4000,widget=forms.Textarea(attrs={'rows':3}))
    confirm = forms.BooleanField(label='Подтверждаю отмену утверждения этого СМГ')


class SMGCancelApproval(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,{'ADMIN','CEO'})
        schedule=get_object_or_404(scope_queryset(DailySchedule.objects.all(),request.user),pk=pk)
        form=CancelForm(request.POST if bound else None,initial={'version':schedule.updated_at.isoformat()})
        if bound and form.is_valid():
            try:
                from .smg_variants import cancel_approval
                cancel_approval(request.user,schedule,form.cleaned_data['reason'],form.cleaned_data['version'])
                messages.success(request,'Утверждение СМГ отменено. Теперь можно утвердить другой вариант. Данные и история сохранены.')
                return redirect('planning:daily_schedule_detail',pk=pk)
            except ValidationError as exc:
                form.add_error(None,'; '.join(exc.messages))
        return render(request,'planning/smg_cancel_approval.html',{'schedule':schedule,'form':form})
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)
