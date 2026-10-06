"""Object-scoped resource entry, shared by labor, equipment and fuel."""

import json
from datetime import date, timedelta
from django import forms as django_forms
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.views.decorators.http import require_POST
from core.mixins import CompanyScopedMixin, CompanyRequiredMixin
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from apps.production import models, forms

CONFIG = {
    "labor": (
        models.LaborPlan,
        models.LaborFact,
        ["brigade"],
        "planned_workers",
        "actual_workers",
    ),
    "equipment": (
        models.EquipmentPlan,
        models.EquipmentFact,
        ["equipment_type", "equipment_number"],
        "planned_count",
        "actual_count",
    ),
    "fuel": (
        models.FuelPlan,
        models.FuelFact,
        ["fuel_type", "equipment_ref"],
        "planned_liters",
        "actual_liters",
    ),
}


class UserFormMixin:
    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}


class ResourceList(CompanyScopedMixin, ListView):
    template_name = "production/object_resources.html"
    context_object_name = "records"
    paginate_by = 100

    def get_queryset(self):
        qs = super().get_queryset().select_related("construction_object")
        obj = self.request.GET.get("construction_object")
        if obj:
            get_object_or_404(
                ConstructionObject, pk=obj, company=self.request.user.company
            )
            qs = qs.filter(construction_object_id=obj)
        day = self.request.GET.get("date")
        if day:
            try:
                qs = qs.filter(date=date.fromisoformat(day))
            except ValueError:
                raise Http404("Некорректная дата")
        return qs.order_by("-date", "pk")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            title=self.model._meta.verbose_name_plural,
            objects=ConstructionObject.objects.filter(
                company=self.request.user.company
            ),
            prefix=self.prefix,
            create_url=reverse("production:" + self.prefix + "_create"),
            field_names=self.field_names,
        )
        ctx["rows"] = [
            {
                "record": r,
                "values": [str(getattr(r, n) or 0) for n in self.field_names],
                "edit": reverse("production:" + self.prefix + "_update", args=[r.pk]),
                "delete": reverse("production:" + self.prefix + "_delete", args=[r.pk]),
            }
            for r in ctx["records"]
        ]
        if self.prefix.endswith("_plan"):
            ctx["range_url"] = reverse("production:" + self.prefix + "_create_range")
        else:
            ctx["daily_url"] = reverse("production:" + self.prefix + "_daily_input")
        ctx["field_labels"] = [
            self.model._meta.get_field(n).verbose_name for n in self.field_names
        ]
        return ctx


class ResourceCreate(UserFormMixin, CompanyRequiredMixin, CreateView):
    template_name = "production/object_resource_form.html"

    def get_initial(self):
        initial = super().get_initial()
        if self.kwargs.get("work_pk"):
            work = get_object_or_404(
                ProjectWork,
                pk=self.kwargs["work_pk"],
                company=self.request.user.company,
            )
            initial["construction_object"] = work.section.construction_object_id
        return initial

    def get_success_url(self):
        return reverse("production:" + self.prefix + "_list")


class ResourceUpdate(UserFormMixin, CompanyScopedMixin, UpdateView):
    template_name = "production/object_resource_form.html"

    def get_success_url(self):
        return reverse("production:" + self.prefix + "_list")


class ResourceDelete(CompanyScopedMixin, DeleteView):
    template_name = "production/resource_confirm_delete.html"

    def get_success_url(self):
        return reverse("production:" + self.prefix + "_list")


class ResourceRange(ResourceCreate):
    def form_valid(self, form):
        values = {
            k: v
            for k, v in form.cleaned_data.items()
            if k not in ("date_start", "date_end")
        }
        days = (
            form.cleaned_data["date_end"] - form.cleaned_data["date_start"]
        ).days + 1
        if days > 3660:
            form.add_error(None, "Диапазон не должен превышать десять лет.")
            return self.form_invalid(form)
        try:
            with transaction.atomic():
                for i in range(days):
                    row = self.model(
                        company=self.request.user.company,
                        date=form.cleaned_data["date_start"] + timedelta(days=i),
                        **values,
                    )
                    row.full_clean()
                    row.save()
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
            return self.form_invalid(form)
        return redirect(self.get_success_url())


class ResourceBulk(View):
    """Use ordinary validated forms; never mutate foreign keys from raw input."""

    def records(self, request, **kwargs):
        qs = self.model.objects.filter(company=request.user.company)
        if kwargs.get("date_str"):
            try:
                qs = qs.filter(date=date.fromisoformat(kwargs["date_str"]))
            except ValueError:
                raise Http404()
        for key, field in [
            ("brigade_id", "brigade_id"),
            ("equipment_type_id", "equipment_type_id"),
            ("fuel_type", "fuel_type"),
        ]:
            if key in kwargs:
                if key != "fuel_type":
                    related = self.model._meta.get_field(field[:-3]).remote_field.model
                    get_object_or_404(
                        related, pk=kwargs[key], company=request.user.company
                    )
                qs = qs.filter(**{field: kwargs[key]})
        obj = request.GET.get("construction_object") or request.POST.get(
            "construction_object"
        )
        if obj:
            get_object_or_404(ConstructionObject, pk=obj, company=request.user.company)
            qs = qs.filter(construction_object_id=obj)
        for key, lookup in [("date_start", "date__gte"), ("date_end", "date__lte")]:
            if request.GET.get(key):
                try:
                    qs = qs.filter(**{lookup: date.fromisoformat(request.GET[key])})
                except ValueError:
                    raise Http404()
        return qs.order_by("date", "pk")

    def get(self, request, **kwargs):
        rows = [
            (r, self.form_class(instance=r, user=request.user, prefix=str(r.pk)))
            for r in self.records(request, **kwargs)
        ]
        return render(
            request,
            "production/resource_bulk.html",
            {"rows": rows, "delete_mode": self.delete_mode},
        )

    def post(self, request, **kwargs):
        qs = self.records(request, **kwargs)
        rows = [
            (
                r,
                self.form_class(
                    request.POST, instance=r, user=request.user, prefix=str(r.pk)
                ),
            )
            for r in qs
        ]
        if self.delete_mode:
            with transaction.atomic():
                qs.delete()
        elif all(form.is_valid() for _, form in rows):
            with transaction.atomic():
                for _, form in rows:
                    form.save()
        else:
            return render(
                request,
                "production/resource_bulk.html",
                {"rows": rows, "delete_mode": False},
                status=400,
            )
        return redirect("production:" + self.prefix + "_list")


class ResourceDaily(View):
    def get(self, request):
        return self.display(
            request, forms.ObjectDateForm(request.GET or None, user=request.user)
        )

    def display(self, request, form, errors=None):
        plans = []
        if form.is_bound and form.is_valid():
            plans = self.plan_model.objects.filter(
                company=request.user.company,
                construction_object=form.cleaned_data["construction_object"],
                date=form.cleaned_data["date"],
            )
        return render(
            request,
            "production/resource_daily.html",
            {"form": form, "plans": plans, "errors": errors},
        )

    def post(self, request):
        form = forms.ObjectDateForm(request.POST, user=request.user)
        if not form.is_valid():
            return self.display(request, form)
        ids = request.POST.getlist("plan_ids") or [
            key[6:] for key in request.POST if key.startswith("value_")
        ]
        try:
            with transaction.atomic():
                for pk in ids:
                    plan = get_object_or_404(
                        self.plan_model,
                        pk=pk,
                        company=request.user.company,
                        construction_object=form.cleaned_data["construction_object"],
                        date=form.cleaned_data["date"],
                    )
                    lookup = {name: getattr(plan, name) for name in self.identities}
                    lookup.update(
                        company=request.user.company,
                        construction_object=plan.construction_object,
                        date=form.cleaned_data["date"],
                    )
                    fact = self.model.objects.filter(**lookup).first() or self.model(
                        **lookup
                    )
                    value = request.POST.get(
                        "value_" + str(pk), request.POST.get("actual_" + str(pk), "")
                    )
                    setattr(fact, self.actual_field, value)
                    for field in ("hourly_rate", "price_per_liter"):
                        if hasattr(plan, field):
                            setattr(fact, field, getattr(plan, field))
                    fact.full_clean()
                    fact.save()
        except ValidationError as exc:
            return self.display(request, form, exc.messages)
        return redirect("production:" + self.prefix + "_list")


def inline_handler(model, allowed):
    @require_POST
    def handler(request):
        try:
            data = json.loads(request.body)
            record = get_object_or_404(
                model,
                pk=data.get("id")
                or data.get("pk")
                or data.get("plan_id")
                or data.get("fact_id"),
                company=request.user.company,
            )
            field = data.get("field")
            if field not in allowed:
                return JsonResponse({"error": "Поле недоступно"}, status=400)
            setattr(record, field, data.get("value"))
            record.full_clean()
            record.save()
            return JsonResponse({"success": True, "value": str(getattr(record, field))})
        except (ValueError, TypeError, ValidationError) as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return handler


# Public class names and routes remain compatible with bookmarked resource screens.
for kind, (plan, fact, identities, planned, actual) in CONFIG.items():
    for suffix, model, fields in [
        ("Plan", plan, forms.RESOURCE_FIELDS[kind]),
        ("Fact", fact, forms.FACT_FIELDS[kind]),
    ]:
        name = model.__name__
        prefix = kind + "_" + suffix.lower()
        form = getattr(forms, name + "Form")
        attributes = {
            "model": model,
            "form_class": form,
            "prefix": prefix,
            "field_names": fields,
            "__module__": __name__,
        }
        for operation, base in [
            ("List", ResourceList),
            ("Create", ResourceCreate),
            ("Update", ResourceUpdate),
            ("Delete", ResourceDelete),
        ]:
            globals()[name + operation + "View"] = type(
                name + operation + "View", (base,), attributes.copy()
            )
        globals()[prefix + "_inline_update"] = inline_handler(
            model, [n for n in fields if n not in identities] + ["comment"]
        )
        for operation in [
            "EditByDate",
            "DeleteByDate",
            "EditRange",
            "DeleteByBrigade" if kind == "labor" else "DeleteByType",
        ]:
            globals()[name + operation + "View"] = type(
                name + operation + "View",
                (ResourceBulk,),
                {**attributes, "delete_mode": operation.startswith("Delete")},
            )
        if suffix == "Plan":
            globals()[name + "RangeCreateView"] = type(
                name + "RangeCreateView",
                (ResourceRange,),
                {**attributes, "form_class": getattr(forms, name + "RangeForm")},
            )
        else:
            globals()[name + "DailyInputView"] = type(
                name + "DailyInputView",
                (ResourceDaily,),
                {
                    **attributes,
                    "plan_model": plan,
                    "identities": identities,
                    "actual_field": actual,
                },
            )


class LegacyList(CompanyScopedMixin, ListView):
    model = models.LegacyResourceRecord
    template_name = "production/legacy_resources.html"

    def get_queryset(self):
        return super().get_queryset().filter(resolved=False)


class LegacyResolve(View):
    def dispatch(self, request, pk):
        self.record = get_object_or_404(
            models.LegacyResourceRecord,
            pk=pk,
            company=request.user.company,
            resolved=False,
        )
        model = getattr(models, self.record.source_model, None)
        if model not in [m for config in CONFIG.values() for m in config[:2]]:
            raise Http404()
        base = getattr(forms, model.__name__ + "Form")

        def validate_unique(form):
            if not form.cleaned_data.get("merge_existing"):
                return base.validate_unique(form)

        self.form_class = type(
            "LegacyResolutionForm",
            (base,),
            {
                "merge_existing": django_forms.BooleanField(
                    required=False,
                    label="Объединить с существующей записью этого объекта, даты и ресурса",
                    help_text="Объёмы и часы складываются. Ставка берётся из этой формы. Исходные записи остаются в журнале.",
                ),
                "validate_unique": validate_unique,
            },
        )
        self.initial = {
            field.name: self.record.payload.get(field.attname)
            for field in model._meta.fields
            if field.name in self.form_class._meta.fields
        }
        return super().dispatch(request, pk)

    def get(self, request, pk):
        return render(
            request,
            "production/object_resource_form.html",
            {
                "form": self.form_class(initial=self.initial, user=request.user),
                "legacy": self.record,
            },
        )

    def post(self, request, pk):
        form = self.form_class(request.POST, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                record = get_object_or_404(
                    models.LegacyResourceRecord.objects.select_for_update(),
                    pk=pk,
                    company=request.user.company,
                    resolved=False,
                )
                row = form.save(commit=False)
                if form.cleaned_data.get("merge_existing"):
                    keys = row._meta.unique_together[0]
                    lookup = {key: getattr(row, key) for key in keys}
                    existing = (
                        type(row)
                        .objects.select_for_update()
                        .filter(company=request.user.company, **lookup)
                        .first()
                    )
                    if existing:
                        for field in form._meta.fields:
                            if field not in keys and field not in (
                                "construction_object",
                                "date",
                                "comment",
                            ):
                                value = getattr(row, field)
                                if (
                                    field.startswith(("planned_", "actual_"))
                                    or field == "machine_hours"
                                ):
                                    value = (getattr(existing, field) or 0) + (
                                        value or 0
                                    )
                                setattr(existing, field, value)
                        existing.comment = "\n".join(
                            filter(
                                None,
                                [
                                    existing.comment,
                                    row.comment,
                                    f"Перенос {record.source_model} №{record.source_pk}",
                                ],
                            )
                        )
                        row = existing
                row.full_clean()
                row.save()
                record.resolved = True
                record.construction_object = row.construction_object
                record.restored_pk = row.pk
                record.save()
            return redirect("production:legacy_resources")
        return render(
            request,
            "production/object_resource_form.html",
            {"form": form, "legacy": self.record},
        )
