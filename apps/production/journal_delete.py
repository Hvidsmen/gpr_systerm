"""Explicit record selection and signed confirmation for journal deletion."""
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from core.permissions import scope_queryset, require_roles, PLAN_ROLES, FACT_ROLES
from core.bulk_delete import inspect_selection

SALT = 'journal-selection-v1'


def journal_config(kind):
    from .views.resources import CONFIG
    from .models import DailyFact
    from apps.planning.models import MonthlyPlan
    configs = {'work_facts': (DailyFact, 'production:fact_list', FACT_ROLES),
               'monthly_plans': (MonthlyPlan, 'planning:plan_list', PLAN_ROLES)}
    for resource, (plan, fact, *_) in CONFIG.items():
        for suffix, model, roles in [('plan', plan, PLAN_ROLES), ('fact', fact, FACT_ROLES)]:
            prefix = resource + '_' + suffix
            configs[prefix] = (model, 'production:' + prefix + '_list', roles)
    return configs[kind]


def monthly_plan_delete_errors(records):
    from apps.planning.signals import preserve_global_provenance
    errors = []
    for record in records:
        for version in record.versions.all():
            try:
                preserve_global_provenance(None, version)
            except ValidationError as error:
                errors.extend(f'{record}: {message}' for message in error.messages)
                break
    return errors


class JournalBulkDelete(View):
    allowed_kinds = ()

    def post(self, request, kind):
        if kind not in self.allowed_kinds:
            raise PermissionDenied
        model, list_route, roles = journal_config(kind)
        require_roles(request.user, roles)
        query = request.POST.get('return_query', '')[:4096]
        back = reverse(list_route) + ('?' + query if query else '')
        confirming = request.POST.get('confirm') == '1'
        if confirming:
            try:
                payload = signing.loads(request.POST.get('selection', ''), salt=SALT, max_age=3600)
                if (payload['user'], payload['company'], payload['kind']) != (request.user.pk, request.user.company_id, kind):
                    raise signing.BadSignature
                ids = payload['ids']
            except (signing.BadSignature, KeyError, TypeError):
                messages.error(request, 'Подтверждение устарело или изменено. Выберите записи заново.')
                return redirect(back)
        else:
            ids = request.POST.getlist('selected')
        if not ids or len(ids) > 1000:
            messages.error(request, 'Выберите от 1 до 1000 записей.')
            return redirect(back)
        field = forms.ModelMultipleChoiceField(queryset=scope_queryset(model.objects.all(), request.user))
        try:
            selected = field.clean(ids)
        except ValidationError:
            raise PermissionDenied('Некоторые выбранные записи недоступны. Обновите журнал.')
        with transaction.atomic():
            selected = selected.select_for_update()
            records, blocked, summary, detached = inspect_selection(selected, kind)
            if kind == 'monthly_plans':
                blocked.extend(monthly_plan_delete_errors(records))
            if len(records) != len(set(map(str, ids))):
                raise PermissionDenied('Состав записей изменился. Обновите журнал.')
            if confirming and not blocked:
                try:
                    with transaction.atomic():
                        selected.delete()
                except (ValidationError, ProtectedError, RestrictedError) as error:
                    blocked.extend(error.messages if isinstance(error, ValidationError) else ['Записи связаны с другими данными. Удаление отменено.'])
                else:
                    messages.success(request, f'Удалено записей: {len(records)}.')
                    return redirect(back)
        token = signing.dumps({'user': request.user.pk, 'company': request.user.company_id,
                               'kind': kind, 'ids': [r.pk for r in records]}, salt=SALT, compress=True)
        return render(request, 'bulk_delete_confirm.html', {
            'title': model._meta.verbose_name_plural, 'records': records, 'blocked': blocked,
            'summary': summary, 'detached': detached, 'selection': token,
            'return_query': query, 'back': back,
        }, status=400 if confirming and blocked else 200)
