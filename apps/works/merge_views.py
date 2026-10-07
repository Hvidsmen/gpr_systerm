"""Selection, norm entry, signed preview and confirmation of work merging."""

from decimal import Decimal
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views import View
from core.permissions import require_roles, PLAN_ROLES
from apps.projects.models import Section
from apps.planning.models import LoadProfile
from .models import MeasurementUnit, WorkGroup
from .merge_service import source_works, preview, apply_merge

SALT = "work-merge-preview-v1"


class MergeForm(forms.Form):
    name = forms.CharField(label="Название составной работы", max_length=255)
    section = forms.ModelChoiceField(label="Раздел", queryset=Section.objects.none())
    unit = forms.ChoiceField(label="Единица составной работы")
    work_group = forms.ModelChoiceField(
        label="Группа работ", queryset=WorkGroup.objects.none(), required=False
    )
    allow_fractional = forms.BooleanField(
        label="Учитывать дробные единицы", required=False, initial=True
    )

    def __init__(self, *args, company, works, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["section"].queryset = Section.objects.filter(
            company=company, construction_object=works[0].section.construction_object
        )
        self.fields["work_group"].queryset = WorkGroup.objects.filter(company=company)
        units = MeasurementUnit.objects.filter(company=company)
        self.fields["unit"].choices = [(unit.symbol, str(unit)) for unit in units]
        self.norm_rows = []
        for work in works:
            norm = f"norm_{work.pk}"
            profile = f"profile_{work.pk}"
            self.fields[norm] = forms.DecimalField(
                label="Норматив на единицу работы",
                max_digits=10,
                decimal_places=3,
                min_value=Decimal(".001"),
                initial=1,
            )
            self.fields[profile] = forms.ModelChoiceField(
                label="Профиль подработы",
                queryset=LoadProfile.objects.filter(company=company),
                initial=work.load_profile_id,
            )
            self.norm_rows.append(
                {"work": work, "norm": self[norm], "profile": self[profile]}
            )
        for name, field in self.fields.items():
            field.widget.attrs["class"] = (
                "form-check-input"
                if name == "allow_fractional"
                else (
                    "form-select form-select-sm"
                    if isinstance(field, forms.ChoiceField)
                    else "form-control form-control-sm"
                )
            )

    def clean(self):
        values = super().clean()
        from apps.planning.workspace_services import captured_profile

        for name, value in list(values.items()):
            if name.startswith("profile_") and value:
                try:
                    captured_profile(value)
                except ValidationError as error:
                    self.add_error(name, error)
        return values


class WorkMergeView(View):
    def get(self, request):
        require_roles(request.user, PLAN_ROLES)
        messages.info(
            request,
            "Выберите простые работы в списке и нажмите «Объединить в составную».",
        )
        return redirect("works:work_list")

    def post(self, request):
        require_roles(request.user, PLAN_ROLES)
        token = request.POST.get("preview", "")
        if request.POST.get("merge_stage") == "confirm":
            try:
                payload = signing.loads(token, salt=SALT, max_age=3600)
                if (
                    payload["user"] != request.user.pk
                    or payload["company"] != request.user.company_id
                ):
                    raise signing.BadSignature
                works = source_works(request.user, payload["ids"])
                form = MergeForm(
                    payload["data"], company=request.user.company, works=works
                )
                if not form.is_valid():
                    raise ValidationError(
                        "Настройки объединения изменились. Выполните расчёт заново."
                    )
                parent, revisions = apply_merge(
                    request.user,
                    [w.pk for w in works],
                    form.cleaned_data,
                    payload["fingerprint"],
                )
                messages.success(
                    request,
                    f"Работы объединены. Факт перенесён; новых черновиков планов: {len(revisions)}. Проверьте и согласуйте их.",
                )
                return redirect("works:work_detail", pk=parent.pk)
            except (signing.BadSignature, KeyError, TypeError, ValueError):
                messages.error(
                    request, "Просмотр устарел или изменён. Выберите работы заново."
                )
                return redirect("works:work_list")
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
                return redirect("works:work_list")
        try:
            works = source_works(request.user, request.POST.getlist("selected"))
        except (ValidationError, ValueError, TypeError) as error:
            messages.error(
                request,
                (
                    "; ".join(error.messages)
                    if isinstance(error, ValidationError)
                    else "Неверный выбор работ."
                ),
            )
            return redirect("works:work_list")
        selected = request.POST.get("merge_stage") == "select"
        form = MergeForm(
            None if selected else request.POST,
            company=request.user.company,
            works=works,
            initial={
                "name": (works[0].name + " — составная")[:255],
                "section": works[0].section_id,
                "unit": "шт",
                "allow_fractional": True,
                "work_group": works[0].work_group_id,
            },
        )
        context = {
            "form": form,
            "sources": works,
            "norm_rows": form.norm_rows,
            "object": works[0].section.construction_object,
        }
        if not selected and form.is_valid():
            try:
                report = preview(request.user, works, form.cleaned_data)
                context.update(report)
                data = {
                    name: (
                        str(value.pk)
                        if hasattr(value, "pk")
                        else str(value) if value is not None else ""
                    )
                    for name, value in form.cleaned_data.items()
                }
                data["allow_fractional"] = (
                    "on" if form.cleaned_data["allow_fractional"] else ""
                )
                context["preview"] = signing.dumps(
                    {
                        "user": request.user.pk,
                        "company": request.user.company_id,
                        "ids": [w.pk for w in works],
                        "data": data,
                        "fingerprint": report["fingerprint"],
                    },
                    salt=SALT,
                    compress=True,
                )
            except ValidationError as error:
                form.add_error(None, error)
        return render(request, "works/work_merge.html", context)
