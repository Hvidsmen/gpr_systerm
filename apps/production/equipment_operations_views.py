from urllib.parse import urlencode
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_GET
from core.permissions import READ_ROLES, require_roles, role_code, scope_queryset
from apps.resources.models import Equipment
from apps.works.models import ProjectWork, ProjectWorkItem
from .forms import ObjectDateForm
from .models import EquipmentAssignment, EquipmentStateChange
from . import equipment_operations as operations

DATE_WIDGET = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})


class AssignmentFields(forms.ModelForm):
    def __init__(self, *args, obj, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company, self.instance.construction_object = obj.company, obj
        self.fields['work'].queryset = ProjectWork.objects.filter(company=obj.company,
            section__construction_object=obj, merged_source__isnull=True)
        self.fields['work_item'].queryset = ProjectWorkItem.objects.filter(company=obj.company,
            project_work__section__construction_object=obj, project_work__merged_source__isnull=True)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control')
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs['rows'] = 2


class AssignmentCreateForm(AssignmentFields):
    state = forms.ChoiceField(label='Состояние на начало назначения', choices=[('KEEP', 'Сохранить текущее состояние')]+EquipmentStateChange.STATES,
        help_text='Берётся история состояний или последний подтверждённый факт. Если их ещё нет — «Работает». Можно явно выбрать другое состояние.')
    state_reason = forms.CharField(label='Причина простоя / ремонта', required=False, max_length=4000)
    class Meta:
        model = EquipmentAssignment
        fields = ['equipment', 'start_date', 'end_date', 'shift', 'general', 'work', 'work_item', 'activity']
        widgets = {'start_date': DATE_WIDGET, 'end_date': DATE_WIDGET}
    def __init__(self, *args, obj, **kwargs):
        super().__init__(*args, obj=obj, **kwargs)
        self.fields['equipment'].queryset = Equipment.objects.filter(company=obj.company, is_active=True)
        self.fields['start_date'].widget.attrs['max'] = timezone.localdate().isoformat()
    def clean(self):
        data = super().clean()
        if data.get('state') in {'IDLE', 'REPAIR'} and not data.get('state_reason', '').strip():
            self.add_error('state_reason', 'Укажите причину простоя или ремонта.')
        return data


class AssignmentChangeForm(AssignmentFields):
    effective_date = forms.DateField(label='Изменить работу с даты', widget=DATE_WIDGET)
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)
    class Meta:
        model = EquipmentAssignment
        fields = ['general', 'work', 'work_item', 'activity']


class StateForm(forms.Form):
    effective_date = forms.DateField(label='Состояние действует с даты', widget=DATE_WIDGET)
    state = forms.ChoiceField(label='Состояние', choices=EquipmentStateChange.STATES, widget=forms.Select(attrs={'class': 'form-select'}))
    reason = forms.CharField(label='Причина / пояснение', required=False, max_length=4000,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)


class CloseForm(forms.Form):
    last_day = forms.DateField(label='Последний день назначения (включительно)', widget=DATE_WIDGET)
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)


class CancelForm(forms.Form):
    reason = forms.CharField(label='Причина отмены', max_length=4000,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}))
    revision = forms.IntegerField(widget=forms.HiddenInput, min_value=1)


@login_required
@require_http_methods(['GET', 'POST'])
def assignments(request):
    require_roles(request.user, operations.OPERATORS if request.method == 'POST' else READ_ROLES | operations.OPERATORS)
    data = request.POST if request.method == 'POST' else request.GET or None
    selector = ObjectDateForm(data, user=request.user, initial={'date': timezone.localdate()})
    context = {'selector': selector, 'editable': role_code(request.user) in operations.OPERATORS}
    if not selector.is_bound or not selector.is_valid():
        return render(request, 'production/equipment_assignments.html', context, status=400 if request.method == 'POST' else 200)
    obj, day = selector.cleaned_data['construction_object'], selector.cleaned_data['date']
    action = request.POST.get('action') if request.method == 'POST' else None
    target = None
    if action and action != 'create':
        try:
            pk = int(request.POST.get('assignment', ''))
        except ValueError:
            raise Http404('Назначение не найдено.')
        target = get_object_or_404(scope_queryset(EquipmentAssignment.objects.all(), request.user),
            pk=pk, construction_object=obj)
    create = AssignmentCreateForm(request.POST if action == 'create' else None, obj=obj,
        initial={'start_date': min(day, timezone.localdate()), 'shift': 1, 'state': 'KEEP'}, auto_id='id_new_%s')
    bound_form = None
    if target:
        klass = {'state': StateForm, 'close': CloseForm, 'change': AssignmentChangeForm, 'cancel': CancelForm}.get(action)
        if not klass:
            raise Http404('Действие не найдено.')
        kwargs = {'obj': obj, 'instance': target} if action == 'change' else {}
        bound_form = klass(request.POST, auto_id=f'id_{action}_{target.pk}_%s', **kwargs)
    form = create if action == 'create' else bound_form
    if action and form and form.is_valid():
        try:
            values = form.cleaned_data
            if action == 'create':
                operations.create_assignment(request.user, form.save(commit=False), values['state'], values['state_reason'])
            elif action == 'state':
                operations.change_state(request.user, target, values['revision'], values['effective_date'], values['state'], values['reason'])
            elif action == 'close':
                operations.close_assignment(request.user, target, values['revision'], values['last_day'])
            elif action == 'cancel':
                operations.cancel_assignment(request.user, target, values['revision'], values['reason'])
            else:
                operations.change_assignment(request.user, target, values['revision'], values['effective_date'],
                    **{key: values[key] for key in ['work', 'work_item', 'general', 'activity']})
            result_day = values['effective_date'] if action in {'state', 'change'} else values['start_date'] if action == 'create' else day
            messages.success(request, {'create': 'Назначение техники сохранено.',
                'state': 'Изменение состояния записано в историю с указанного дня и смены.',
                'change': 'Назначение работы изменено с указанного дня.',
                'close': 'Последний день назначения сохранён. Со следующего дня техника не будет добавляться в подготовку факта.',
                'cancel': 'Ошибочное назначение отменено, запись об отмене сохранена.'}[action])
            return redirect(reverse('production:equipment_assignment_list')+'?'+urlencode({'construction_object': obj.pk, 'date': result_day.isoformat()}))
        except ValidationError as exc:
            form.add_error(None, ValidationError('; '.join(exc.messages)))
    baseline, _ = operations.prepare(obj, day)
    rows = []
    for row in baseline.values():
        assignment = row['assignment']; current = row['state_record']
        initial = {'revision': assignment.revision, 'effective_date': min(day, timezone.localdate()),
                   'last_day': day, 'state': row['values']['state']}
        row['state_form'] = bound_form if action == 'state' and target.pk == assignment.pk else StateForm(initial=initial, auto_id=f'id_state_{assignment.pk}_%s')
        row['close_form'] = bound_form if action == 'close' and target.pk == assignment.pk else CloseForm(initial=initial, auto_id=f'id_close_{assignment.pk}_%s')
        row['change_form'] = bound_form if action == 'change' and target.pk == assignment.pk else AssignmentChangeForm(obj=obj, instance=assignment, initial=initial, auto_id=f'id_change_{assignment.pk}_%s')
        row['cancel_form'] = bound_form if action == 'cancel' and target.pk == assignment.pk else CancelForm(initial=initial, auto_id=f'id_cancel_{assignment.pk}_%s')
        row['state_label'] = dict(EquipmentStateChange.STATES)[row['values']['state']]
        row['open_action'] = action if target and target.pk == assignment.pk else ''
        rows.append(row)
    context.update(object=obj, day=day, rows=rows, create_form=create,
        fact_url=reverse('production:fact_day_workspace')+'?'+urlencode({'construction_object': obj.pk, 'date': day.isoformat(), 'section': 'mechanization'}))
    if target and not any(row['assignment'].pk == target.pk for row in rows) and form.errors:
        context['outside_form'] = form
    return render(request, 'production/equipment_assignments.html', context, status=400 if action else 200)


@login_required
@require_GET
def history(request, pk):
    require_roles(request.user, READ_ROLES | operations.OPERATORS)
    visible = scope_queryset(EquipmentAssignment.objects.all(), request.user).filter(equipment_id=pk)
    if not visible.exists():
        raise Http404('История техники недоступна.')
    machine = get_object_or_404(Equipment, pk=pk, company=request.user.company)
    return render(request, 'production/equipment_history.html', {'machine': machine,
        'assignments': visible.select_related('construction_object', 'work', 'work_item', 'created_by').order_by('-start_date', '-pk'),
        'changes': EquipmentStateChange.objects.filter(assignment__in=visible).select_related('recorded_by', 'assignment__construction_object')})
