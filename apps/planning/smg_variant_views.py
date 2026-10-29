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


class SMGCopyVariant(View):
    def post(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        schedule = get_object_or_404(scope_queryset(DailySchedule.objects.all(), request.user), pk=pk)
        try:
            new = independent_smg.revise(request.user, schedule, independent=True)
            messages.success(request, 'Создан независимый черновик. Исходный вариант и его согласование сохранены.')
            return redirect('planning:daily_schedule_detail', pk=new.pk)
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            return redirect('planning:daily_schedule_detail', pk=pk)


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
