import json
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from core.permissions import PLAN_ROLES, require_roles
from .models import ProjectWork, WorkPrice
from .prices import change_price, price_on, can_backdate_price
from .price_sources import source_field, source_note, identity


class PriceForm(forms.Form):
    price = forms.DecimalField(
        label="Новая цена, ₽", max_digits=12, decimal_places=2, min_value=0
    )
    effective_from = forms.DateField(
        label="Действует с",
        required=False,
        help_text="Для исправления дата исходной записи сохраняется автоматически.",
        widget=forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
    )
    reason = forms.CharField(
        label="Основание / комментарий", required=False, max_length=500
    )
    corrects = forms.ModelChoiceField(
        label="Исправление прежней записи",
        required=False,
        queryset=WorkPrice.objects.none(),
        empty_label="Новая цена",
    )

    def __init__(self, *args, work, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.work = work
        self.user = user
        if can_backdate_price(user):
            self.fields["effective_from"].help_text = "Администратор может указать прошедшую дату. Запись сохранится в истории; цены зафиксированных планов сохранятся."
        self.fields["price_source"] = source_field(work.company, work.pk)
        if self.is_bound and self.data.get(self.add_prefix("price_source")):
            self.fields["price"].required = False
        self.fields["corrects"].queryset = work.price_history.all()
        self.fields["corrects"].widget.attrs["data-effective-dates"] = json.dumps({str(entry.pk): entry.effective_from.isoformat() for entry in work.price_history.all()})
        self.fields["corrects"].label_from_instance = (
            lambda entry: f"{'Первоначальная' if entry.effective_from.year == 1 else entry.effective_from.strftime('%d.%m.%Y')} · {entry.price} ₽ · запись {entry.pk}"
        )
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"

    def clean(self):
        data = super().clean()
        day, correction = data.get("effective_from"), data.get("corrects")
        if correction:
            data["effective_from"] = correction.effective_from
            if not data.get("reason"):
                self.add_error("reason", "Укажите основание исправления.")
        elif not day:
            self.add_error("effective_from", "Укажите дату начала действия новой цены.")
        elif day < timezone.localdate() and not can_backdate_price(self.user):
            self.add_error(
                "effective_from",
                "Для изменения прошлой цены выберите исправляемую запись.",
            )
        source = data.get("price_source")
        if source:
            if identity(source.unit) != identity(self.work.unit):
                self.add_error("price_source", "Единицы измерения работ не совпадают.")
            elif data.get("effective_from"):
                data["price"] = price_on(source, data["effective_from"])
                data["reason"] = (source_note(source) + (" · " + data["reason"] if data.get("reason") else ""))[:500]
        return data


class WorkPriceChange(View):
    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, PLAN_ROLES)
        self.work = get_object_or_404(
            ProjectWork,
            pk=kwargs["pk"],
            company=request.user.company,
            merged_source__isnull=True,
        )
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        form = PriceForm(
            work=self.work,
            user=request.user,
            initial={
                "price": price_on(self.work),
                "effective_from": timezone.localdate(),
            },
        )
        return render(
            request, "works/price_form.html", {"form": form, "work": self.work}
        )

    def post(self, request, pk):
        form = PriceForm(request.POST, work=self.work, user=request.user)
        if form.is_valid():
            try:
                change_price(request.user, self.work.pk, **{key: value for key, value in form.cleaned_data.items() if key != "price_source"})
                messages.success(
                    request,
                    "Цена сохранена в истории. Согласованные планы не изменены.",
                )
                return redirect("works:work_detail", pk=pk)
            except ValidationError as error:
                form.add_error(None, error)
        return render(
            request, "works/price_form.html", {"form": form, "work": self.work}
        )
