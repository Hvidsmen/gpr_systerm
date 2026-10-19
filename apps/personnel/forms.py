from django import forms
from .models import Board, Person, StaffingGroup, StaffingSlot


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            f.widget.attrs['class'] = 'form-select' if isinstance(f.widget, forms.Select) else 'form-control'
            if isinstance(f, forms.DateField): f.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})
            if isinstance(f.widget, forms.Textarea): f.widget.attrs['rows'] = 3


class BoardForm(StyledModelForm):
    class Meta:
        model = Board
        fields = ['name', 'project', 'site']


class PersonForm(StyledModelForm):
    class Meta:
        model = Person
        fields = ['full_name', 'employee_number', 'phone', 'notes']


class GroupForm(StyledModelForm):
    class Meta:
        model = StaffingGroup
        fields = ['position', 'attendance_plan', 'staffing_plan']


class SlotForm(StyledModelForm):
    class Meta:
        model = StaffingSlot
        fields = ['person', 'camp', 'status', 'departure_date', 'arrival_date', 'pattern', 'comment']
    def __init__(self, *args, board, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['person'].queryset = Person.objects.filter(company_id=board.company_id)
        self.fields['pattern'].queryset = board.patterns.all()


class CalendarForm(forms.Form):
    start = forms.DateField(label='С даты', widget=forms.DateInput(attrs={'type':'date', 'class':'form-control'}))
    end = forms.DateField(label='По дату', widget=forms.DateInput(attrs={'type':'date', 'class':'form-control'}))
    code = forms.CharField(label='Код дня: часы, МВ, МО, В или ДП. Пусто — вернуть график', max_length=10, required=False, widget=forms.TextInput(attrs={'class':'form-control'}))
    def clean(self):
        data = super().clean(); start, end = data.get('start'), data.get('end')
        if start and end and (end < start or (end-start).days > 730): raise forms.ValidationError('Период должен быть от 1 дня до 2 лет.')
        code = data.get('code', '').strip().upper()
        if code and code not in {'МО','МВ','В','ДП'}:
            try:
                number = float(code.replace(',', '.'))
                if not 0 <= number <= 24: raise ValueError
                code = str(number).rstrip('0').rstrip('.') if '.' in str(number) else str(number)
            except ValueError: raise forms.ValidationError('Укажите часы от 0 до 24 или код МВ, МО, В, ДП.')
        data['code'] = code
        return data


class ImportForm(forms.Form):
    name = forms.CharField(label='Название таблицы', max_length=200, initial='Нефтепровод', widget=forms.TextInput(attrs={'class':'form-control'}))
    file = forms.FileField(label='Файл Excel, лист «Нефтепровод»', widget=forms.ClearableFileInput(attrs={'class':'form-control','accept':'.xlsx'}))
    confirm = forms.BooleanField(label='Подтверждаю загрузку показанных данных', required=False)


class PatternForm(forms.Form):
    name = forms.CharField(label='Название графика', max_length=150)
    kind = forms.CharField(label='Вид графика (например, М или Ж)', max_length=20, required=False)
    anchor = forms.DateField(label='Первый день вахтового цикла', widget=forms.DateInput(attrs={'type':'date'}))
    start = forms.DateField(label='Календарь с', widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='Календарь по', widget=forms.DateInput(attrs={'type':'date'}))
    on_days = forms.IntegerField(label='Дней на вахте', min_value=1, max_value=365, initial=60)
    off_days = forms.IntegerField(label='Дней межвахтового отдыха', min_value=1, max_value=365, initial=30)
    hours = forms.DecimalField(label='Часов в каждый день вахты', min_value=0, max_value=24, decimal_places=2, max_digits=4, initial=10)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for f in self.fields.values():f.widget.attrs['class']='form-control'
    def clean(self):
        data=super().clean()
        if data.get('start') and data.get('end') and (data['end']<data['start'] or (data['end']-data['start']).days>730):raise forms.ValidationError('Календарь должен быть от 1 дня до 2 лет.')
        return data
