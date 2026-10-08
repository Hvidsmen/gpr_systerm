from django import forms
from apps.planning.models import GlobalPlanVersion
from .models import RotationPlan, RotationRole, RotationPerson


class PlanForm(forms.ModelForm):
    class Meta:
        model = RotationPlan
        fields = ['title', 'source', 'start', 'end']
        widgets = {name: forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}) for name in ['start', 'end']}

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = user.company
        self.fields['source'].queryset = GlobalPlanVersion.objects.filter(company=user.company).select_related('construction_object')
        self.fields['source'].label_from_instance = lambda v: f'{v.construction_object} · {v} · {v.start_date:%d.%m.%Y} — {v.end_date:%d.%m.%Y}'
        self.fields['source'].help_text = 'Потребность берётся из людей общего плана. Для черновика выполняется предварительный расчёт.'


class RoleForm(forms.ModelForm):
    class Meta:
        model = RotationRole
        fields = ['on_days', 'off_days', 'anchor']
        widgets = {'anchor': forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'})}


class PersonForm(forms.ModelForm):
    class Meta:
        model = RotationPerson
        fields = ['name', 'on_days', 'off_days', 'anchor']
        widgets = {'anchor': forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'})}
