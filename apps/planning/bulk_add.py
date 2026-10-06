"""Bulk selection of company resources and object works for a planning version."""
from datetime import date
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from apps.production.forms import CompanyFormMixin
from apps.resources.models import Brigade, EquipmentType
from apps.works.models import ProjectWork
from .models import GlobalPlanVersion, ResourceMonthAllocation, WorkMonthAllocation
from .workspace_services import months_between
from .workspace_views import version_for

TITLES = {'works':'Работы', 'labor':'Люди — бригады', 'equipment':'Техника', 'fuel':'ГСМ'}


def editable_months(version):
    return [month for month in months_between(version.start_date, version.end_date)
        if not version.planning_month or month >= version.planning_month
        and (version.scenario == 'REMAINING' or month == version.planning_month)]


class BulkAddForm(CompanyFormMixin, forms.Form):
    scope = forms.ChoiceField(label='Куда добавить', choices=[('month','В выбранный месяц')])
    month = forms.ChoiceField(label='Месяц')

    def __init__(self, *args, version, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['month'].choices = [(m.isoformat(), m.strftime('%m.%Y')) for m in editable_months(version)]
        if version.version_kind == 'BASELINE':
            self.fields['scope'].choices.append(('period','Во весь период'))
        if kind == 'fuel':
            self.fields['items'] = forms.MultipleChoiceField(label='Виды ГСМ',
                choices=ResourceMonthAllocation._meta.get_field('fuel_type').choices,
                widget=forms.CheckboxSelectMultiple())
            self.fields['equipment_ref'] = forms.CharField(label='Техника для ГСМ (необязательно)', max_length=150, required=False)
        else:
            querysets = {
                'works': lambda: ProjectWork.objects.filter(company=self.company, section__construction_object=version.construction_object),
                'labor': lambda: Brigade.objects.filter(company=self.company, is_active=True),
                'equipment': lambda: EquipmentType.objects.filter(company=self.company, is_active=True),
            }
            self.fields['items'] = forms.ModelMultipleChoiceField(label=TITLES[kind],
                queryset=querysets[kind]().order_by('name'), widget=forms.CheckboxSelectMultiple())
            if kind == 'works':
                self.fields['items'].label_from_instance = lambda w: f'{w.code} — {w.name} ({w.unit}, {w.get_kind_display()})'
            if kind == 'equipment':
                self.fields['equipment_number'] = forms.CharField(label='Номер машины (необязательно)', max_length=50, required=False)
        for name, field in self.fields.items():
            if name != 'items':
                field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'


class WorkspaceBulkAdd(View):
    def setup_form(self, request, pk, kind, bound=False):
        from django.http import Http404
        if kind not in TITLES:
            raise Http404('Неизвестный вид ресурса')
        version = version_for(request, pk)
        if version.status not in ['DRAFT','REJECTED']:
            raise PermissionDenied('Редактировать можно только черновик или отклонённый план.')
        initial = {'scope':request.GET.get('scope','month'), 'month':request.GET.get('month') or (version.planning_month or editable_months(version)[0]).isoformat()}
        form = BulkAddForm(request.POST if bound else None, initial=initial, user=request.user, version=version, kind=kind)
        return version, form

    def display(self, request, version, form, kind, status=200):
        return render(request, 'planning/workspace_bulk_add.html', {'version':version, 'form':form, 'title':TITLES[kind], 'kind':kind}, status=status)

    def get(self, request, pk, kind):
        version, form = self.setup_form(request, pk, kind)
        return self.display(request, version, form, kind)

    def post(self, request, pk, kind):
        version, form = self.setup_form(request, pk, kind, True)
        if not form.is_valid():
            return self.display(request, version, form, kind, 400)
        month = date.fromisoformat(form.cleaned_data['month'])
        months = months_between(version.start_date, version.end_date) if form.cleaned_data['scope'] == 'period' else [month]
        added = skipped = 0
        with transaction.atomic():
            locked = GlobalPlanVersion.objects.select_for_update().get(pk=version.pk, company=request.user.company)
            if locked.status not in ['DRAFT','REJECTED']:
                raise PermissionDenied('Версия уже отправлена на согласование.')
            for item in form.cleaned_data['items']:
                for target in months:
                    if kind == 'works':
                        _, created = WorkMonthAllocation.objects.get_or_create(company=locked.company, version=locked, month=target, work=item)
                    else:
                        identity = {'labor':lambda:{'brigade':item},
                            'equipment':lambda:{'equipment_type':item, 'equipment_number':form.cleaned_data['equipment_number']},
                            'fuel':lambda:{'fuel_type':item, 'equipment_ref':form.cleaned_data['equipment_ref']}}[kind]()
                        _, created = ResourceMonthAllocation.objects.get_or_create(company=locked.company, version=locked, month=target, kind=kind, **identity)
                    added += int(created)
                    skipped += int(not created)
            if added:
                locked.snapshot = {}
                locked.save(update_fields=['snapshot'])
        messages.success(request, f'Добавлено месячных строк: {added}. Уже существующих: {skipped}. Введённые объёмы сохранены.')
        return redirect(reverse('planning:workspace_edit', args=[version.pk])+'?month='+month.isoformat())
