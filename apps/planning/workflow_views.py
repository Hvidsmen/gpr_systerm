from django.contrib import messages
from django.views.generic import View
from django.shortcuts import get_object_or_404, redirect, render
from .models import MonthlyPlan, PlanVersion, DailyPlan, LoadProfile
from .services import PlanGeneratorService, PlanWorkflowService, PlanRevisionService


class VersionCreateView(View):
    """Простое создание новой версии путём копирования последней."""

    def get(self, request, plan_pk):
        """Показываем страницу подтверждения."""
        plan = get_object_or_404(
            MonthlyPlan, company=self.request.user.company, pk=plan_pk
        )
        last_version = plan.versions.order_by("-version_number").first()

        if not last_version:
            messages.error(request, "У плана нет версий для копирования.")
            return redirect("planning:plan_detail", pk=plan.pk)

        return render(
            request,
            "planning/version_create_confirm.html",
            {
                "plan": plan,
                "last_version": last_version,
                "new_version_number": plan.versions.count() + 1,
            },
        )

    def post(self, request, plan_pk):
        """Создаём новую версию."""
        plan = get_object_or_404(
            MonthlyPlan, company=self.request.user.company, pk=plan_pk
        )
        last_version = plan.versions.order_by("-version_number").first()

        if not last_version:
            messages.error(request, "У плана нет версий для копирования.")
            return redirect("planning:plan_detail", pk=plan.pk)

        # Создаём новую версию
        new_number = plan.versions.count() + 1
        new_version = PlanVersion.objects.create(
            monthly_plan=plan,
            company=plan.company,
            version_number=new_number,
            status="DRAFT",
            created_by=request.user,
            comment=f"Копия версии v{last_version.version_number}",
        )

        # Копируем дневные планы
        for dp in last_version.daily_plans.all():
            DailyPlan.objects.create(
                plan_version=new_version,
                company=new_version.company,
                work_item=dp.work_item,
                date=dp.date,
                workday_number=dp.workday_number,
                planned_quantity=dp.planned_quantity,
                planned_value=dp.planned_value,
            )

        messages.success(
            request,
            f"✅ Создана версия v{new_number}. Теперь вы можете изменить параметры и перегенерировать.",
        )
        return redirect("planning:version_detail", pk=new_version.pk)


class VersionRegenerateView(View):
    """Validate all requested changes before replacing a draft plan."""

    def post(self, request, pk):
        from django.db import transaction
        from django.forms.models import model_to_dict
        from .forms import MonthlyPlanForm

        version = get_object_or_404(PlanVersion, company=request.user.company, pk=pk)
        if version.is_immutable or version.status == "SUBMITTED":
            messages.error(
                request, "Отправленную или утверждённую версию нельзя перегенерировать."
            )
            return redirect("planning:version_detail", pk=pk)
        plan = version.monthly_plan
        data = model_to_dict(plan, fields=MonthlyPlanForm.Meta.fields)
        data.update(
            {
                key: request.POST[key]
                for key in MonthlyPlanForm.Meta.fields
                if key in request.POST
            }
        )
        form = MonthlyPlanForm(data, instance=plan)
        form.fields["project_work"].queryset = form.fields[
            "project_work"
        ].queryset.filter(company=request.user.company)
        if not form.is_valid():
            messages.error(
                request,
                "; ".join(
                    str(error) for errors in form.errors.values() for error in errors
                ),
            )
            return redirect("planning:version_detail", pk=pk)
        try:
            with transaction.atomic():
                form.save()
                work = plan.project_work
                for item in work.items.all():
                    profile_id = request.POST.get(f"profile_{item.pk}")
                    if profile_id:
                        item.load_profile = get_object_or_404(
                            LoadProfile, company=request.user.company, pk=profile_id
                        )
                        item.full_clean()
                        item.save()
                count = PlanGeneratorService.generate(version)
            messages.success(request, f"Сгенерировано {count} дневных записей.")
        except Exception as exc:
            messages.error(request, str(exc))
        return redirect("planning:version_detail", pk=pk)


class VersionGenerateView(View):
    """Перегенерация дневного плана."""

    def post(self, request, pk):
        version = get_object_or_404(
            PlanVersion, company=self.request.user.company, pk=pk
        )
        if version.is_immutable:
            messages.error(request, "Утверждённую версию нельзя перегенерировать")
            return redirect("planning:version_detail", pk=pk)

        try:
            count = PlanGeneratorService.generate(version)
            messages.success(request, f"Сгенерировано {count} записей")
        except Exception as e:
            messages.error(request, f"Ошибка: {e}")

        return redirect("planning:version_detail", pk=pk)


class VersionSubmitView(View):
    """Отправка версии на согласование."""

    def post(self, request, pk):
        version = get_object_or_404(
            PlanVersion, company=self.request.user.company, pk=pk
        )
        try:
            PlanWorkflowService.submit(version, request.user)
            messages.success(request, "План отправлен на согласование")
        except Exception as e:
            messages.error(request, str(e))
        return redirect("planning:version_detail", pk=pk)


class VersionApproveView(View):
    """Утверждение версии плана."""

    def post(self, request, pk):
        version = get_object_or_404(
            PlanVersion, company=self.request.user.company, pk=pk
        )
        if not request.user.is_manager() and not request.user.is_admin():
            messages.error(request, "Нет прав для утверждения")
            return redirect("planning:version_detail", pk=pk)
        try:
            PlanWorkflowService.approve(version, request.user)
            messages.success(request, "План утверждён и заблокирован")
        except Exception as e:
            messages.error(request, str(e))
        return redirect("planning:version_detail", pk=pk)


class VersionCompleteView(View):
    """Завершение утверждённой версии без изменения плановых показателей."""

    def post(self, request, pk):
        version = get_object_or_404(PlanVersion, company=request.user.company, pk=pk)
        if not (request.user.is_manager() or request.user.is_admin()):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Нет прав для завершения плана.")
        try:
            PlanWorkflowService.complete(version, request.user)
            messages.success(request, "План завершён. Утверждённые данные сохранены.")
        except Exception as e:
            messages.error(request, str(e))
        return redirect("planning:version_detail", pk=pk)


class VersionRejectView(View):
    """Отклонение версии плана."""

    def post(self, request, pk):
        version = get_object_or_404(
            PlanVersion, company=self.request.user.company, pk=pk
        )
        comment = request.POST.get("comment", "")
        try:
            PlanWorkflowService.reject(version, request.user, comment)
            messages.success(request, "План отклонён и возвращён в черновики")
        except Exception as e:
            messages.error(request, str(e))
        return redirect("planning:version_detail", pk=pk)


class VersionRevisionView(View):
    """Создание ревизии утверждённой версии."""

    def post(self, request, pk):
        version = get_object_or_404(
            PlanVersion, company=self.request.user.company, pk=pk
        )
        try:
            new_version = PlanRevisionService.create_revision(version, request.user)
            messages.success(request, f"Создана ревизия v{new_version.version_number}")
            return redirect("planning:version_detail", pk=new_version.pk)
        except Exception as e:
            messages.error(request, str(e))
        return redirect("planning:version_detail", pk=pk)


class SetBaselineVersionView(View):
    """Установить версию как актуальную (baseline)."""

    def post(self, request, pk):
        version = get_object_or_404(
            PlanVersion, company=self.request.user.company, pk=pk
        )

        # Снимаем baseline со всех версий этого плана
        PlanVersion.objects.filter(
            company=self.request.user.company,
            monthly_plan=version.monthly_plan,
            is_baseline=True,
        ).update(is_baseline=False)

        # Устанавливаем baseline на выбранную версию
        version.is_baseline = True
        version.save(update_fields=["is_baseline"])

        messages.success(
            request, f"Версия v{version.version_number} установлена как актуальная"
        )
        return redirect("planning:plan_detail", pk=version.monthly_plan_id)
