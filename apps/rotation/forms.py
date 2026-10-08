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


class StatusForm(forms.Form):
    day = forms.DateField(label='Дата', widget=forms.DateInput(format='%Y-%m-%d', attrs={'type':'date'}))
    status = forms.ChoiceField(label='Статус', choices=[('AUTO','По графику'),('ON','Вахта'),('OFF','Отдых')])

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person
        plan = person.position.plan
        self.fields['day'].widget.attrs.update(min=plan.start.isoformat(), max=plan.end.isoformat())
        self.fields['status'].help_text = 'Ручной статус перекрывает график на выбранный день. «По графику» удаляет ручное изменение.'

    def clean_day(self):
        day = self.cleaned_data['day']
        plan = self.person.position.plan
        if not plan.start <= day <= plan.end:
            raise forms.ValidationError('Дата за пределами плана перевахты.')
        return day
