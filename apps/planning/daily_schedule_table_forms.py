from django import forms
from apps.works.models import ProjectWork
from apps.resources.models import Brigade,EquipmentType
from .models import ResourceMonthAllocation


class AddEntryForm(forms.Form):
    kind=forms.ChoiceField(label='Раздел',choices=[('work','Работы'),('labor','Должность'),('equipment','Техника'),('fuel','ГСМ')])
    selection=forms.ChoiceField(label='Объект / комплекс / работа',required=False)
    work=forms.ModelChoiceField(label='Работа',queryset=ProjectWork.objects.none(),required=False)
    brigade=forms.ModelChoiceField(label='Должность',queryset=Brigade.objects.none(),required=False)
    equipment=forms.ModelChoiceField(label='Вид техники',queryset=EquipmentType.objects.none(),required=False)
    reference=forms.CharField(label='Номер техники / привязка ГСМ',max_length=50,required=False)
    fuel=forms.ChoiceField(label='Вид ГСМ',choices=[('','Выберите значение'),*ResourceMonthAllocation._meta.get_field('fuel_type').choices],required=False)
    rate=forms.DecimalField(label='Ставка за час / цена за литр',max_digits=12,decimal_places=2,min_value=0,initial=0,required=False)

    def __init__(self,*args,schedule,**kwargs):
        super().__init__(*args,**kwargs)
        from .estimate_catalog import alternatives
        self.fields['work'].queryset=alternatives(schedule)
        from .work_selection import choices
        self.fields['selection'].choices=choices(alternatives(schedule))
        self.fields['work'].widget=forms.HiddenInput()
        self.fields['work'].label_from_instance=lambda work:f'{work.section.construction_object} · {work.section.name} · {work.name} · {work.unit} · ID {work.pk}'
        self.fields['brigade'].queryset=Brigade.objects.filter(company_id=schedule.company_id)
        from apps.resources.equipment_levels import smg_types
        self.fields['equipment'].queryset=smg_types(EquipmentType.objects.filter(company_id=schedule.company_id,merge_source__isnull=True))

    def clean(self):
        data=super().clean();field={'work':'work','labor':'brigade','equipment':'equipment','fuel':'fuel'}.get(data.get('kind'))
        if field and not data.get(field) and not (field=='work' and data.get('selection')):self.add_error(field,'Выберите позицию для добавления.')
        if data.get('rate') is None:data['rate']=0
        return data
