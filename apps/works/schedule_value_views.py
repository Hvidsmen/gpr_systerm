"""Separate preview/confirm entry points for project totals and opening fact."""
from datetime import date
from decimal import Decimal
from django.urls import reverse
from uuid import uuid4
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from apps.planning.models import ObjectWorkPlan
from apps.production.models import WorkOpeningBalance
from apps.production import work_opening
from .models import ProjectWork
from .schedule_import import ImportForm, parse_schedule
from . import schedule_loading as service

SALT = 'works.schedule-values.v1.'


class ValueImportForm(ImportForm):
    def __init__(self, *args, user, metric, plan=None, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        self.fields.pop('load_profile')
        self.fields['section'].required = False
        self.fields['section'].empty_label = 'Все разделы объекта'
        self.fields['section'].help_text = 'Укажите раздел, если одинаковые комплексы встречаются в нескольких разделах.'
        self.fields['file'].widget.attrs['accept'] = '.xlsx'
        if plan:
            self.fields['construction_object'].initial = plan.construction_object_id
            self.fields['construction_object'].disabled = True
            from apps.projects.models import Section
            self.fields['section'].queryset = scope_queryset(Section.objects.filter(construction_object_id=plan.construction_object_id), user)
        if metric == 'opening':
            self.fields['date'] = forms.DateField(label='ВНО на начало дня', required=False,
                widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
                help_text='Можно оставить пустым: дата будет взята из заголовка месяца Excel и показана в предпросмотре.')
            self.fields['reason'] = forms.CharField(label='Основание ввода или исправления', max_length=1000,
                initial='Входящий факт из графика работ', widget=forms.Textarea(attrs={'rows': 2}))
        elif not plan:
            self.fields['name'] = forms.CharField(label='Название нового плана', max_length=255, initial='План на весь объект')
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'form-select' if isinstance(field.widget, forms.Select) else 'form-control')


def token_payload(user, metric, token):
    try:
        data = signing.loads(token, salt=SALT + metric, max_age=900)
    except (signing.BadSignature, TypeError):
        raise ValidationError('Предпросмотр недействителен или истёк. Загрузите файл заново.')
    if any(data.get(k) != value for k, value in {'user': user.pk, 'company': user.company_id, 'metric': metric}.items()):
        raise ValidationError('Предпросмотр относится к другому пользователю или виду загрузки.')
    return data


@login_required
@require_http_methods(['GET', 'POST'])
def schedule_value_import(request, metric, pk=None):
    require_roles(request.user, {'ADMIN'} if metric == 'opening' else PLAN_ROLES)
    plan = get_object_or_404(scope_queryset(ObjectWorkPlan.objects.select_related('construction_object'), request.user), pk=pk) if pk else None
    initial = {'construction_object': plan.construction_object_id if plan else request.GET.get('construction_object'), 'section': request.GET.get('section')}
    form = ValueImportForm(request.POST if request.method == 'POST' else None, request.FILES or None,
        user=request.user, metric=metric, plan=plan, initial=initial)
    context = {'form': form, 'metric': metric, 'plan': plan, 'opening_tab': metric == 'opening', 'object_plan_tab': metric == 'plan'}
    if request.method == 'POST':
        try:
            if request.POST.get('action') == 'confirm':
                data = token_payload(request.user, metric, request.POST.get('token', ''))
                if data.get('plan') != (plan.pk if plan else None):
                    raise ValidationError('Предпросмотр относится к другому плану.')
                if metric == 'opening':
                    changed = service.apply_opening(request.user, data)
                    messages.success(request, 'Входящие остатки сохранены. Дневной факт не изменён.' if changed else 'Эта загрузка уже выполнена.')
                    return redirect(reverse('production:work_opening_list') + '?construction_object=' + str(data['object']))
                result, changed = service.apply_plan(request.user, data)
                messages.success(request, 'Плановые объёмы загружены. Справочник и факт не изменены.' if changed else 'Эта загрузка уже выполнена.')
                return redirect('planning:object_plan_detail', pk=result.pk)
            if form.is_valid():
                rows, ignored = parse_schedule(form.cleaned_data['file'], metric=metric)
                obj = form.cleaned_data['construction_object']
                section = form.cleaned_data['section']
                rows = service.match_rows(request.user, obj, rows, section)
                data = {'user': request.user.pk, 'company': request.user.company_id, 'metric': metric,
                    'object': obj.pk, 'section': section.pk if section else None, 'rows': rows,
                    'filename': form.cleaned_data['file'].name[:255], 'nonce': str(uuid4()), 'plan': plan.pk if plan else None}
                if metric == 'opening':
                    day = form.cleaned_data.get('date')
                    if not day:
                        periods = {r['file_month'] for r in rows}
                        if len(periods) != 1 or None in periods:
                            raise ValidationError('Не удалось определить единую дату ВНО из файла. Укажите дату «На начало дня».')
                        day = date.fromisoformat(periods.pop())
                    data.update(date=day.isoformat(), reason=form.cleaned_data['reason'], opening_revisions={})
                    works = {w.pk: w for w in ProjectWork.objects.filter(company=request.user.company, pk__in={r.get('work_id') for r in rows})}
                    existing = {(r.project_work_id, r.work_item_id): r for r in WorkOpeningBalance.objects.filter(company=request.user.company, project_work_id__in=works)}
                    for work in works.values():
                        data['opening_revisions'][str(work.pk)] = work_opening.revision(work)
                    for row in rows:
                        old = existing.get((row.get('work_id'), row.get('item_id')))
                        row['before'] = str(old.quantity) if old else None
                        row['before_date'] = old.date if old else None
                        if not row['issue']:
                            try:
                                WorkOpeningBalance(company=request.user.company, project_work=works[row['work_id']], work_item_id=row['item_id'], date=day, quantity=Decimal(row['volume'])).clean()
                            except ValidationError as exc:
                                row['issue'] = '; '.join(exc.messages)
                    context['opening_date'] = day
                else:
                    data.update(name=plan.name if plan else form.cleaned_data['name'], plan_revision=service.plan_revision(plan) if plan else None)
                    values = {(r.work_id, r.work_item_id): r.quantity for r in plan.rows.all()} if plan else {}
                    for row in rows:
                        old = values.get((row.get('work_id'), row.get('item_id')))
                        row['before'] = str(old) if old is not None else None
                issues = sum(bool(r['issue']) for r in rows)
                # Preview-only date objects are rendered separately, not signed.
                signed_rows = [{k: v for k, v in r.items() if k != 'before_date'} for r in rows]
                data['rows'] = signed_rows
                context.update(rows=rows, object=obj, issues=issues, ignored=ignored,
                    nonzero=sum(float(r['volume']) != 0 for r in rows), daily_ignored=sum(r.get('daily_values_ignored', 0) for r in rows))
                if not issues:
                    context['token'] = signing.dumps(data, salt=SALT + metric, compress=True)
        except ValidationError as exc:
            context['error'] = '; '.join(exc.messages)
    return render(request, 'works/schedule_value_import.html', context)
