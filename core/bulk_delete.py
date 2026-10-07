"""Two-step, company-scoped bulk deletion for works and reference catalogs."""

from django import forms
from django.contrib import messages
from django.contrib.admin.utils import NestedObjects
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views import View
from core.permissions import PLAN_ROLES, require_roles

SALT = "catalog-bulk-delete-v1"


def config(kind):
    from apps.works.models import ProjectWork, WorkGroup, MeasurementUnit
    from apps.resources.models import (
        Brigade,
        BrigadeGroup,
        BrigadeMacroGroup,
        EquipmentType,
        EquipmentCategory,
        Employee,
    )
    from apps.planning.models import LoadProfile, ProductionCalendar

    return {
        "works": (ProjectWork, "works:work_list", "Работы"),
        "work_groups": (WorkGroup, "works:group_list", "Группы работ"),
        "units": (MeasurementUnit, "works:unit_list", "Единицы измерения"),
        "brigades": (Brigade, "resources:brigade_list", "Бригады"),
        "brigade_groups": (
            BrigadeGroup,
            "resources:brigade_group_list",
            "Группы бригад",
        ),
        "brigade_macros": (
            BrigadeMacroGroup,
            "resources:brigade_macro_group_list",
            "Макрогруппы бригад",
        ),
        "equipment": (EquipmentType, "resources:equipment_type_list", "Виды техники"),
        "equipment_categories": (
            EquipmentCategory,
            "resources:equipment_category_list",
            "Категории техники",
        ),
        "employees": (Employee, "resources:employee_list", "Сотрудники"),
        "profiles": (LoadProfile, "planning:profile_list", "Профили нагрузки"),
        "calendars": (ProductionCalendar, "planning:calendar_list", "Календари"),
    }[kind]


def inspect_selection(query, kind):
    records = list(query)
    if not records:
        raise PermissionDenied("Выбранные записи уже удалены. Обновите список.")
    blocked = []
    for record in records:
        if kind == "works" and (
            record.workmonthallocation_set.exists()
            or record.daily_facts.exists()
            or record.monthly_plans.filter(
                versions__status__in=["APPROVED", "COMPLETED"]
            ).exists()
        ):
            blocked.append(
                f"{record}: есть факт или включение в план. Сначала удалите связанные записи планирования."
            )
        if kind == "units":
            from apps.works.unit_views import unit_usage

            if unit_usage(record):
                blocked.append(
                    f"{record}: единица используется в работах, шаблонах или ресурсах."
                )
    collector = NestedObjects(using=query.db)
    collector.collect(records)
    for record in collector.protected:
        blocked.append(
            f"{record._meta.verbose_name}: {record} — используется связанная запись."
        )
    for related in collector.data.values():
        if any(
            hasattr(obj, "company_id") and obj.company_id != records[0].company_id
            for obj in related
        ):
            blocked.append(
                "Связанные записи принадлежат другой компании. Удаление недоступно."
            )
    summary = [
        {
            "label": (
                "подработы"
                if model._meta.label == "works.ProjectWorkItem"
                else model._meta.verbose_name_plural
            ),
            "count": len(values),
        }
        for model, values in collector.data.items()
    ]
    detached = []
    for (field, value), batches in collector.field_updates.items():
        for batch in batches:
            if hasattr(batch, "model") and any(
                f.name == "company" for f in batch.model._meta.fields
            ):
                foreign = batch.exclude(company_id=records[0].company_id).exists()
            else:
                foreign = not hasattr(batch, "model") and any(
                    hasattr(obj, "company_id")
                    and obj.company_id != records[0].company_id
                    for obj in batch
                )
            if foreign:
                blocked.append(
                    "Связанные записи принадлежат другой компании. Удаление недоступно."
                )
        count = sum(
            (
                batch.count()
                if hasattr(batch, "count") and hasattr(batch, "model")
                else len(batch)
            )
            for batch in batches
        )
        if count:
            detached.append(
                {
                    "label": field.model._meta.verbose_name_plural,
                    "field": field.verbose_name,
                    "count": count,
                }
            )
    return records, blocked, summary, detached


class BulkDelete(View):
    allowed_kinds = ()

    def post(self, request, kind):
        require_roles(request.user, PLAN_ROLES)
        if kind not in self.allowed_kinds:
            raise PermissionDenied
        model, list_route, title = config(kind)
        back = reverse(list_route)
        return_query = request.POST.get("return_query", "")
        if return_query:
            back += "?" + return_query
        token = request.POST.get("selection", "")
        if request.POST.get("confirm"):
            try:
                payload = signing.loads(token, salt=SALT, max_age=3600)
                if (payload["user"], payload["company"], payload["kind"]) != (
                    request.user.pk,
                    request.user.company_id,
                    kind,
                ):
                    raise signing.BadSignature
                ids = payload["ids"]
            except (signing.BadSignature, KeyError, TypeError):
                messages.error(
                    request,
                    "Подтверждение устарело или изменено. Выберите записи заново.",
                )
                return redirect(back)
        else:
            ids = request.POST.getlist("selected")
        if not ids or len(ids) > 1000:
            messages.error(request, "Выберите от 1 до 1000 записей.")
            return redirect(back)
        field = forms.ModelMultipleChoiceField(
            queryset=model.objects.filter(company=request.user.company)
        )
        try:
            selected = field.clean(ids)
        except forms.ValidationError:
            raise PermissionDenied(
                "Некоторые выбранные записи недоступны. Обновите список."
            )
        # Recheck dependencies under the same transaction as the final deletion.
        with transaction.atomic():
            query = (
                selected.select_for_update()
                if request.POST.get("confirm")
                else selected
            )
            records, blocked, summary, detached = inspect_selection(query, kind)
            if len(records) != len({str(pk) for pk in ids}):
                raise PermissionDenied(
                    "Состав выбранных записей изменился. Обновите список."
                )
            if request.POST.get("confirm") and not blocked:
                try:
                    with transaction.atomic():
                        selected.delete()
                except (ProtectedError, RestrictedError):
                    blocked.append(
                        "Связанные записи изменились. Удаление отменено; обновите список."
                    )
                else:
                    messages.success(
                        request, f"Удалено выбранных записей: {len(records)}."
                    )
                    return redirect(back)
        token = signing.dumps(
            {
                "user": request.user.pk,
                "company": request.user.company_id,
                "kind": kind,
                "ids": [r.pk for r in records],
            },
            salt=SALT,
            compress=True,
        )
        return render(
            request,
            "bulk_delete_confirm.html",
            {
                "title": title,
                "records": records,
                "blocked": blocked,
                "summary": summary,
                "detached": detached,
                "selection": token,
                "back": back,
                "return_query": return_query,
            },
            status=400 if request.POST.get("confirm") and blocked else 200,
        )
