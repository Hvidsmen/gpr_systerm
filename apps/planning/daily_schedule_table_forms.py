from django import forms
from apps.works.models import ProjectWork
from apps.resources.models import Brigade,EquipmentType
from .models import ResourceMonthAllocation


class AddEntryForm(forms.Form):
    kind=forms.ChoiceField(label='Раздел',choices=[('work','Работы'),('labor','Должность'),('equipment','Техника'),('fuel','ГСМ')])
    selection=forms.ChoiceField(label='Объект / комплекс / работа',required=False)
    work=forms.ModelChoiceField(label='Работа',queryset=ProjectWork.objects.none(),required=False)
    brigade=forms.ModelChoiceField(label='Должность',queryset=Brigade.objects.none(),required=False)
    equipment=forms.ModelChoiceField(label='Техника для планирования',queryset=EquipmentType.objects.none(),required=False)
    reference=forms.CharField(max_length=50,required=False,widget=forms.HiddenInput())
    fuel=forms.ChoiceField(label='Вид ГСМ',choices=[('','Выберите значение'),*ResourceMonthAllocation._meta.get_field('fuel_type').choices],required=False)
    add_crew=forms.BooleanField(label='Добавить экипаж из справочника',required=False,initial=True,widget=forms.CheckboxInput(attrs={'class':'form-check-input'}))
    rate=forms.DecimalField(label='Ставка за час / цена за литр',max_digits=12,decimal_places=2,min_value=0,initial=0,required=False)

    def __init__(self,*args,schedule,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        from .estimate_catalog import alternatives
        from apps.works.planning_access import planning_works
        self.fields['work'].queryset=planning_works(alternatives(schedule),user,whole=True)
        from .work_selection import choices
        self.fields['selection'].choices=choices(alternatives(schedule),include_construction_object=True,user=user)
        from .subobject_smg import parent_reference
        from .models import DailySchedule
        if isinstance(schedule,DailySchedule) and parent_reference(schedule):self.fields['selection'].label='Работа · каталог субобъекта и общего ГПР'
        self.fields['work'].widget=forms.HiddenInput()
        self.fields['work'].label_from_instance=lambda work:f'{work.section.construction_object} · {work.section.name} · {work.name} · {work.unit} · ID {work.pk}'
        from apps.resources.position_catalog import planning_brigades
        self.fields['brigade'].queryset=planning_brigades(schedule.company)
        self.fields['brigade'].help_text='Актуальные должности и подтверждённые новые позиции справочника.'
        from apps.resources.equipment_levels import gpr_types
        self.fields['equipment'].queryset=gpr_types(EquipmentType.objects.filter(company_id=schedule.company_id,merge_source__isnull=True,is_active=True))
        from django.db.models import Q
        from apps.resources.catalogue_review import resource_ids
        pending_ids = resource_ids(schedule)
        self.fields['brigade'].queryset = Brigade.objects.filter(company_id=schedule.company_id).filter(Q(pk__in=self.fields['brigade'].queryset.values('pk')) | Q(pk__in=pending_ids['labor']))
        self.fields['equipment'].queryset = EquipmentType.objects.filter(company_id=schedule.company_id,parent__isnull=True).filter(Q(pk__in=self.fields['equipment'].queryset.values('pk')) | Q(pk__in=pending_ids['equipment']))
        self.auto_id='plan-add-%s'

    def clean(self):
        data=super().clean();field={'work':'work','labor':'brigade','equipment':'equipment','fuel':'fuel'}.get(data.get('kind'))
        if field and not data.get(field) and not (field=='work' and data.get('selection')):self.add_error(field,'Выберите позицию для добавления.')
        if data.get('rate') is None:data['rate']=0
        data['reference']=''
        return data
