"""Company-scoped editing and deletion of measurement units."""

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from core.permissions import PLAN_ROLES, require_roles
from apps.resources.models import Brigade, EquipmentType, FuelType
from .models import (
    MeasurementUnit,
    ProjectWork,
    ProjectWorkItem,
    WorkTemplate,
    WorkTemplateItem,
)
from .catalogs import UnitForm


def unit_usage(unit):
    return sum(
        model.objects.filter(company=unit.company, unit=unit.symbol).count()
        for model in [
            ProjectWork,
            ProjectWorkItem,
            WorkTemplate,
            WorkTemplateItem,
            Brigade,
            EquipmentType,
            FuelType,
        ]
    )


class UnitUpdate(View):
    def get(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        unit = get_object_or_404(MeasurementUnit, company=request.user.company, pk=pk)
        return render(
            request,
            "works/unit_form.html",
            {
                "object": unit,
                "form": UnitForm(instance=unit, company=request.user.company),
                "usage": unit_usage(unit),
            },
        )

    def post(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        if request.POST.get('action')=='confirm_rename':
            from .unit_rename import apply
            from django.core.exceptions import ValidationError
            try:
                apply(request.user,pk,request.POST.get('token',''),request.POST.get('equivalent')=='on')
                messages.success(request,'Обозначение обновлено в справочнике и связанных записях. Объёмы и цены сохранены.')
                return redirect('works:unit_list')
            except ValidationError as exc:
                unit=get_object_or_404(MeasurementUnit,company=request.user.company,pk=pk)
                return render(request,'works/unit_form.html',{'object':unit,'form':UnitForm(instance=unit,company=request.user.company),'usage':unit_usage(unit),'error':'; '.join(exc.messages)},status=400)
        with transaction.atomic():
            unit = get_object_or_404(
                MeasurementUnit.objects.select_for_update(),
                company=request.user.company,
                pk=pk,
            )
            old_symbol = unit.symbol
            usage = unit_usage(unit)
            form = UnitForm(request.POST, instance=unit, company=request.user.company)
            if form.is_valid():
                if form.cleaned_data["symbol"] != old_symbol and usage:
                    from .unit_rename import preview
                    unit.symbol=old_symbol
                    original=MeasurementUnit.objects.get(pk=pk,company=request.user.company)
                    return render(request,'works/unit_form.html',{'object':original,'form':form,'usage':usage,'preview':preview(request.user,original,form.cleaned_data)})
                else:
                    try:
                        with transaction.atomic():
                            form.save()
                    except IntegrityError:
                        form.add_error(
                            "symbol", "Такая единица уже есть в справочнике."
                        )
                    else:
                        messages.success(request, "Единица измерения сохранена.")
                        return redirect("works:unit_list")
        return render(
            request,
            "works/unit_form.html",
            {"object": unit, "form": form, "usage": usage},
        )


class UnitDelete(View):
    def get(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        unit = get_object_or_404(MeasurementUnit, company=request.user.company, pk=pk)
        return render(
            request,
            "works/unit_confirm_delete.html",
            {"object": unit, "usage": unit_usage(unit)},
        )

    def post(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        with transaction.atomic():
            unit = get_object_or_404(
                MeasurementUnit.objects.select_for_update(),
                company=request.user.company,
                pk=pk,
            )
            usage = unit_usage(unit)
            if not usage:
                unit.delete()
                messages.success(request, "Единица измерения удалена.")
                return redirect("works:unit_list")
        return render(
            request,
            "works/unit_confirm_delete.html",
            {"object": unit, "usage": usage},
            status=400,
        )
