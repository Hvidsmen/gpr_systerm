"""One object/day transaction for all four fact categories."""
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.forms import BaseModelFormSet, modelformset_factory
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from core.mixins import CompanyRequiredMixin
from apps.works.models import ProjectWork
from apps.planning.workspace_services import current_workspace_version, virtual_resource_plan
from apps.production import forms as production_forms
from apps.production.models import DailyFact
from .resources import CONFIG


class DayRowMixin:
    def __init__(self, *args, construction_object, day, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('date', None)
        self.fields.pop('construction_object', None)
        self.instance.date = day
        if hasattr(self.instance, 'construction_object_id'):
            self.instance.construction_object = construction_object
        if 'project_work' in self.fields:
            self.fields['project_work'].queryset = self.fields['project_work'].queryset.filter(
                section__construction_object=construction_object
            )
            self.fields['work_item'].queryset = self.fields['work_item'].queryset.filter(
                project_work__section__construction_object=construction_object
            )
        for name in ('actual_quantity', 'actual_workers', 'actual_count', 'actual_liters'):
            if name in self.fields:
                self.fields[name].required = True
                # Initial quantities in extra rows must be empty, not model defaults.
                if not self.instance.pk and name not in self.initial:
                    self.initial[name] = None
        for name in ('comment',):
            if name in self.fields:
                self.fields[name].widget = forms.Textarea(attrs={'rows': 2, 'class': 'form-control'})


class ScopedDayFormSet(BaseModelFormSet):
    def add_fields(self, form, index):
        super().add_fields(form, index)
        form.fields[self.model._meta.pk.name].queryset = self.get_queryset()


class FactDayWorkspaceView(CompanyRequiredMixin, View):
    template_name = 'production/day_workspace.html'

    def get(self, request):
        selector = production_forms.ObjectDateForm(
            request.GET or None, user=request.user, initial={'date': timezone.localdate()}
        )
        return self.display(request, selector)

    def groups(self, request, obj, day, data=None):
        company = self.get_company()
        work_facts = DailyFact.objects.filter(
            company=company, project_work__section__construction_object=obj, date=day
        ).order_by('pk')
        existing = set(work_facts.values_list('project_work_id', 'work_item_id'))
        initial = []
        hints = {"works": {}}
        for work in ProjectWork.objects.filter(company=company, section__construction_object=obj).prefetch_related('items'):
            items = [item for item in work.items.all() if item.company_id == company.pk] if work.kind == 'COMPOSITE' else [None]
            for item in items:
                key = (work.pk, item.pk if item else None)
                hints['works'][tuple(str(value or '') for value in key)] = (
                    f'Единица: {item.unit}. Норматив на единицу работы: {item.quantity_per_unit}.'
                    if item else f'Единица: {work.unit}.'
                )
                if key not in existing:
                    initial.append({'project_work': key[0], 'work_item': key[1], 'actual_quantity': None})
        configs = [('works', 'Работы и подработы', DailyFact, production_forms.DailyFactForm, work_facts, initial)]
        version = current_workspace_version(company, obj, day)
        for kind, (plan_model, model, identities, planned, actual) in CONFIG.items():
            facts = model.objects.filter(company=company, construction_object=obj, date=day).order_by('pk')
            keys = set(tuple(getattr(row, name + '_id' if name in ('brigade', 'equipment_type') else name) for name in identities) for row in facts)
            if version:
                plans = [virtual_resource_plan(version, kind, i, plan_model)
                         for i, row in enumerate(version.snapshot.get('resources', {}).get(kind, []))
                         if row['date'] == day.isoformat()]
            else:
                plans = plan_model.objects.filter(company=company, construction_object=obj, date=day)
            initial = []
            hints[kind] = {}
            for plan in plans:
                values = {name: getattr(plan, name + '_id' if name in ('brigade', 'equipment_type') else name) for name in identities}
                key = tuple(values.values())
                hours_field = {'labor': 'planned_hours', 'equipment': 'planned_machine_hours'}.get(kind)
                hours = getattr(plan, hours_field) if hours_field else None
                units = {'labor': 'чел.', 'equipment': 'ед.', 'fuel': 'л'}
                hint = f'План за день: {getattr(plan, planned)} {units[kind]}'
                if hours is not None:
                    hint += f'; {hours} ' + ('чел-час' if kind == 'labor' else 'маш-час')
                hints[kind][tuple(str(value or '') for value in key)] = hint
                if key in keys:
                    continue
                keys.add(key)
                for rate in ('hourly_rate', 'price_per_liter'):
                    if hasattr(plan, rate):
                        values[rate] = getattr(plan, rate)
                values[actual] = None
                initial.append(values)
            configs.append((kind, {'labor': 'Люди', 'equipment': 'Техника', 'fuel': 'ГСМ'}[kind], model,
                            getattr(production_forms, model.__name__ + 'Form'), facts, initial))
        groups = []
        for prefix, title, model, base_form, qs, initial in configs:
            row_form = type('Day' + model.__name__ + 'Form', (DayRowMixin, base_form), {})
            factory = modelformset_factory(model, form=row_form, formset=ScopedDayFormSet, extra=len(initial) + (0 if prefix == 'works' else 1),
                                          can_delete=True, max_num=2000, absolute_max=2000)
            formset = factory(data, prefix=prefix, queryset=qs, initial=initial,
                              form_kwargs={'user': request.user, 'construction_object': obj, 'day': day})
            identity_fields = ['project_work', 'work_item'] if prefix == 'works' else CONFIG[prefix][2]
            for row in formset:
                key = tuple(str(row[name].value() or '') for name in identity_fields)
                row.day_hint = hints[prefix].get(key, '')
            groups.append({'prefix': prefix, 'title': title, 'formset': formset})
        return groups

    def display(self, request, selector, groups=None, error=None, status=200):
        if groups is None and selector.is_bound and selector.is_valid():
            groups = self.groups(request, selector.cleaned_data['construction_object'], selector.cleaned_data['date'])
        return render(request, self.template_name, {'selector': selector, 'groups': groups, 'error': error}, status=status)

    def post(self, request):
        selector = production_forms.ObjectDateForm(request.POST, user=request.user)
        if not selector.is_valid():
            return self.display(request, selector, status=400)
        obj, day = selector.cleaned_data['construction_object'], selector.cleaned_data['date']
        groups = self.groups(request, obj, day, request.POST)
        # Evaluate every category so errors are shown together.
        valid = [group['formset'].is_valid() for group in groups]
        if not all(valid):
            return self.display(request, selector, groups, status=400)
        try:
            with transaction.atomic():
                for group in groups:
                    formset = group['formset']
                    rows = formset.save(commit=False)
                    for row in formset.deleted_objects:
                        row.delete()
                    for row in rows:
                        if isinstance(row, DailyFact) and not row.pk:
                            row.reported_by = request.user
                        row.full_clean()
                        row.save()
        except ValidationError as exc:
            return self.display(request, selector, groups, error='; '.join(exc.messages), status=400)
        except IntegrityError:
            return self.display(request, selector, groups,
                                error='Запись за этот день уже существует. Проверьте повторяющиеся строки или обновите экран.', status=400)
        messages.success(request, 'Все изменения факта объекта за день сохранены.')
        return redirect(reverse('production:fact_day_workspace') + '?' + urlencode({'construction_object': obj.pk, 'date': day.isoformat()}))
