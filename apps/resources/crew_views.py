from django import forms
from django.contrib import messages
from django.db import transaction, IntegrityError
from django.forms import BaseModelFormSet, modelformset_factory
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import READ_ROLES, PLAN_ROLES, require_roles
from .models import Position, EquipmentType, EquipmentCrewPosition


class PositionForm(forms.ModelForm):
    class Meta:
        model=Position
        fields=['name','default_hourly_rate']

    def clean_name(self):
        name=self.cleaned_data['name'].strip()
        if Position.objects.filter(company=self.instance.company,name__iexact=name).exclude(pk=self.instance.pk).exists():raise forms.ValidationError('Должность с таким названием уже есть.')
        return name

    def clean_default_hourly_rate(self):
        value=self.cleaned_data['default_hourly_rate']
        if value<0:raise forms.ValidationError('Ставка не может быть отрицательной.')
        return value


class PositionCatalogue(View):
    def display(self,request,pk=None,bound=False):
        require_roles(request.user,PLAN_ROLES if bound or pk else READ_ROLES)
        if pk:
            position=get_object_or_404(Position,company=request.user.company,pk=pk)
            from .position_catalog import brigade_for
            return redirect('resources:brigade_update',pk=brigade_for(position))
        return redirect('resources:brigade_list')
    def get(self,request,pk=None):return self.display(request,pk)
    def post(self,request,pk=None):return self.display(request,pk,True)


class CrewForm(forms.ModelForm):
    class Meta:
        model=EquipmentCrewPosition
        fields=['position','count','is_replacement']

    def __init__(self,*args,equipment,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=equipment.company;self.instance.equipment_type=equipment
        from .position_catalog import available_positions, position_label
        self.fields['position'].queryset=available_positions(equipment.company)
        self.fields['position'].label_from_instance=position_label
        self.fields['count'].widget.attrs['min']=1


class CrewSet(BaseModelFormSet):
    def clean(self):
        super().clean()
        seen=set()
        for form in self.forms:
            data=getattr(form,'cleaned_data',{})
            position=data.get('position')
            if not position or data.get('DELETE'):continue
            if position.pk in seen:raise forms.ValidationError('Должность уже есть в экипаже. Укажите общую численность в одной строке.')
            seen.add(position.pk)

    def add_fields(self,form,index):
        super().add_fields(form,index)
        if 'id' in form.fields:form.fields['id'].queryset=self.get_queryset()


class EquipmentCrew(View):
    def display(self,request,pk,bound=False):
        require_roles(request.user,PLAN_ROLES if bound else READ_ROLES)
        equipment=get_object_or_404(EquipmentType,pk=pk,company=request.user.company,merge_source__isnull=True)
        Factory=modelformset_factory(EquipmentCrewPosition,form=CrewForm,formset=CrewSet,extra=2,can_delete=True,max_num=100,validate_max=True)
        formset=Factory(request.POST if bound else None,queryset=equipment.crew_positions.filter(company=request.user.company),form_kwargs={'equipment':equipment})
        if bound and formset.is_valid():
            try:
                with transaction.atomic():
                    EquipmentType.objects.select_for_update().get(pk=equipment.pk)
                    formset.save()
                messages.success(request,'Экипаж на одну единицу техники и одну смену сохранен. Существующие планы не пересчитаны.')
                return redirect('resources:equipment_crew',pk=pk)
            except IntegrityError:
                formset._non_form_errors=formset.error_class(['Экипаж изменен в другой вкладке. Обновите страницу.'])
        return render(request,'resources/equipment_crew.html',{'equipment':equipment,'formset':formset})
    def get(self,request,pk):return self.display(request,pk)
    def post(self,request,pk):return self.display(request,pk,True)


class EquipmentCrewEditorMixin:
    """Save the equipment card and its crew as a single transaction."""
    def get_crew_formset(self):
        if not hasattr(self, '_crew_formset'):
            equipment = self.object or EquipmentType(company=self.get_company())
            factory = modelformset_factory(EquipmentCrewPosition, form=CrewForm,
                formset=CrewSet, extra=1, can_delete=True, max_num=100, validate_max=True)
            rows = EquipmentCrewPosition.objects.filter(company=self.get_company(), equipment_type=equipment) if equipment.pk else EquipmentCrewPosition.objects.none()
            self._crew_formset = factory(self.request.POST if self.request.method == 'POST' else None,
                queryset=rows, prefix='crew', form_kwargs={'equipment':equipment})
        return self._crew_formset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['crew_formset'] = self.get_crew_formset()
        return context

    def form_invalid(self, form):
        self.get_crew_formset().is_valid()
        return super().form_invalid(form)

    def form_valid(self, form):
        crew = self.get_crew_formset()
        if not crew.is_valid():
            return self.form_invalid(form)
        try:
            with transaction.atomic():
                if self.object:
                    EquipmentType.objects.select_for_update().get(pk=self.object.pk, company=self.get_company())
                self.object = form.save()
                for row in crew.forms:
                    row.instance.equipment_type = self.object
                crew.save()
        except IntegrityError:
            form.add_error(None, 'Экипаж изменён в другой вкладке. Обновите страницу.')
            return self.form_invalid(form)
        messages.success(self.request, 'Техника и экипаж сохранены. Существующие назначения в планах не изменены.')
        return redirect(self.get_success_url())
