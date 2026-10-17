from datetime import date
from django import forms
from django.forms import formset_factory
from django.core.exceptions import ValidationError, PermissionDenied
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import require_roles, READ_ROLES, PLAN_ROLES
from .models import GlobalPlanVersion
from .hierarchy_services import month_specs, work_volume, distribution, create_smg, resource_distribution


def source_for(request, pk):
    return get_object_or_404(GlobalPlanVersion.objects.defer('snapshot').select_related('construction_object', 'workspace'), pk=pk, company=request.user.company)


class DistributionFilter(forms.Form):
    month = forms.DateField(label='Месяц ГПР', widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}, format='%Y-%m-%d'))


class DistributionDetail(View):
    def get(self, request, pk):
        require_roles(request.user, READ_ROLES)
        version = source_for(request, pk)
        form = DistributionFilter(request.GET or {'month': (version.planning_month or (version.workspace.continuation_start if version.workspace_id and version.workspace.continuation_start else version.start_date.replace(day=1))).isoformat()})
        month = (version.planning_month or (version.workspace.continuation_start if version.workspace_id and version.workspace.continuation_start else version.start_date.replace(day=1)))
        if request.GET and form.is_valid():
            month = form.cleaned_data['month']
        workspaces, rows, resource_rows = [], [], []
        if not form.is_bound or form.is_valid():
            try:
                workspaces, rows = distribution(version, month, include_facts=True)
                resource_rows = resource_distribution(version, month, workspaces)
            except ValidationError as exc:
                form.add_error(None, exc)
        return render(request, 'planning/gpr_distribution.html', {'version': version, 'form': form, 'month': month, 'workspaces': workspaces, 'rows': rows, 'resource_rows': resource_rows})


class SmgCreate(View):
    def get(self, request, pk):
        return self.display(request, pk)

    def post(self, request, pk):
        return self.display(request, pk, request.POST)

    def display(self, request, pk, data=None):
        require_roles(request.user, PLAN_ROLES)
        version = source_for(request, pk)
        filter_form = DistributionFilter(request.GET or {'month': (version.planning_month or (version.workspace.continuation_start if version.workspace_id and version.workspace.continuation_start else version.start_date.replace(day=1))).isoformat()})
        month = (version.planning_month or (version.workspace.continuation_start if version.workspace_id and version.workspace.continuation_start else version.start_date.replace(day=1)))
        if request.GET and filter_form.is_valid():
            month = filter_form.cleaned_data['month']
        specs, error = [], None
        if filter_form.is_bound and not filter_form.is_valid():
            error = 'Укажите корректный месяц.'
        else:
            try:
                specs = month_specs(version, month)
            except ValidationError as exc:
                error = '; '.join(exc.messages)
        class ChildForm(forms.Form):
            construction_object = forms.ModelChoiceField(queryset=version.construction_object.children.filter(company=request.user.company), label='Субобъект СМГ')
        specs_by_id = {spec['id']: spec for spec in specs}
        class RowForm(forms.Form):
            work = forms.IntegerField(widget=forms.HiddenInput(), label='Работа ГПР')
            def clean_work(self):
                value = self.cleaned_data['work']
                if value not in specs_by_id:
                    raise forms.ValidationError('Выберите работу исходного ГПР.')
                return value
            quantity = forms.DecimalField(min_value=0, max_digits=15, decimal_places=3, label='Объём СМГ на месяц')
        child_form = ChildForm(data)
        formset = formset_factory(RowForm, extra=0, max_num=max(len(specs), 1), validate_max=True, absolute_max=max(len(specs), 1))(data, initial=[{'work': str(spec['id']), 'quantity': 0} for spec in specs])
        if data is not None and not error:
            valid_child, valid_rows = child_form.is_valid(), formset.is_valid()
            if valid_child and valid_rows:
                quantities = {int(row['work']): row['quantity'] for row in formset.cleaned_data}
                if len(quantities) != len(formset.cleaned_data):
                    error = 'Работы в распределении не должны повторяться.'
                else:
                    try:
                        workspace = create_smg(request.user, version, child_form.cleaned_data['construction_object'], month, quantities)
                        return redirect('planning:workspace_edit', pk=workspace.baseline_version_id)
                    except ValidationError as exc:
                        error = '; '.join(exc.messages)
        for row in formset:
            try:
                spec = specs_by_id.get(int(row['work'].value()))
            except (TypeError, ValueError):
                spec = None
            row.gpr_label = f"{spec['name']} ({spec['unit']}) · ГПР {work_volume(spec)}" if spec else 'Работа ГПР'
        return render(request, 'planning/smg_create.html', {'version': version, 'month': month, 'filter_form': filter_form, 'child_form': child_form, 'formset': formset, 'error': error})


class SmgDaysEdit(View):
    def get(self, request, pk):
        return self.display(request, pk)

    def post(self, request, pk):
        return self.display(request, pk, request.POST)

    def display(self, request, pk, data=None):
        from django.db import transaction
        from decimal import Decimal
        from .models import PlanningWorkspace, WorkMonthAllocation
        from .workspace_services import specification, work_rows, finalize_work
        from .report_services import days_between
        require_roles(request.user, PLAN_ROLES)
        workspace = get_object_or_404(PlanningWorkspace.objects.select_related('baseline_version').defer('baseline_version__snapshot'), pk=pk, company=request.user.company, parent_gpr__isnull=False)
        if workspace.baseline_version.status not in ('DRAFT', 'REJECTED'):
            raise PermissionDenied('Редактировать можно только черновой СМГ или СМГ на доработке.')
        allocations = workspace.baseline_version.work_allocations.select_related('work', 'load_profile').order_by('work__name')
        selected = request.GET.get('work')
        allocation = get_object_or_404(allocations, work_id=selected) if selected else allocations.first()
        if allocation is None:
            return redirect('planning:workspace_detail', pk=pk)
        spec = specification(allocation.work)
        generated = allocation.daily_override if allocation.daily_override is not None else work_rows(workspace, spec, allocation.month, allocation.quantity, allocation.load_profile, allocation.item_quantities)
        values = {(row['date'], row['item_id']): row['quantity'] for row in generated}
        items = spec.get('items', []) if spec['kind'] == 'COMPOSITE' else [{'id': None, 'name': spec['name'], 'unit': spec['unit']}]
        class DailyForm(forms.Form):
            quantity = forms.DecimalField(min_value=0, max_digits=15, decimal_places=3, label='Объём за день')
        identities = [(day, item) for day in days_between(workspace.start_date, workspace.end_date) for item in items]
        formset = formset_factory(DailyForm, extra=0, max_num=len(identities), validate_max=True, absolute_max=len(identities))(data, initial=[{'quantity': values.get((day.isoformat(), item['id']), 0)} for day, item in identities])
        error = None
        if data is not None and formset.is_valid():
            if len(formset.forms) != len(identities):
                error = 'Укажите объёмы всех дней и подработ.'
            else:
                try:
                    with transaction.atomic():
                        from .models import GlobalPlanVersion
                        version = GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=workspace.baseline_version_id)
                        if version.status not in ('DRAFT', 'REJECTED'):
                            raise ValidationError('СМГ уже отправлен на согласование.')
                        allocation = WorkMonthAllocation.objects.select_for_update().get(pk=allocation.pk)
                        rows = [{'date': day.isoformat(), 'item_id': item['id'], 'quantity': str(form.cleaned_data['quantity'])} for (day, item), form in zip(identities, formset.forms)]
                        finalized = finalize_work(spec, rows, workspace)
                        allocation.quantity = sum((Decimal(row['quantity']) for row in finalized['daily']), Decimal(0))
                        allocation.item_quantities = {str(item['id']): str(sum((Decimal(row['quantity']) for row in rows if row['item_id'] == item['id']), Decimal(0))) for item in items} if spec['kind'] == 'COMPOSITE' else {}
                        allocation.daily_override = rows
                        allocation.full_clean()
                        allocation.save()
                        # Monthly edits clear overrides; restore the explicitly entered days
                        # after saving the recalculated monthly quantity.
                        allocation.daily_override = rows
                        allocation.save(update_fields=['daily_override'])
                    return redirect('planning:workspace_detail', pk=workspace.pk)
                except ValidationError as exc:
                    error = '; '.join(exc.messages)
        display_rows = [{'day': day, 'item': item, 'form': form} for (day, item), form in zip(identities, formset.forms)]
        return render(request, 'planning/smg_days.html', {'workspace': workspace, 'allocations': allocations, 'allocation': allocation, 'formset': formset, 'rows': display_rows, 'error': error})
