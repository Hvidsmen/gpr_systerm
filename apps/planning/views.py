from decimal import Decimal

from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404, redirect, render
from core.mixins import CompanyRequiredMixin, CompanyScopedMixin

from .models import MonthlyPlan, PlanVersion, DailyPlan, LoadProfile
from .forms import MonthlyPlanForm
from .services import PlanGeneratorService
from apps.production.models import DailyFact


# =============================================================================
# МЕСЯЧНЫЕ ПЛАНЫ
# =============================================================================


class PlanListView(CompanyScopedMixin, ListView):
    model = MonthlyPlan
    template_name = 'planning/plan_list.html'
    context_object_name = 'plans'
    paginate_by = 20

    def get_queryset(self):
        qs = MonthlyPlan.objects.filter(company=self.request.user.company).select_related(
            'project_work',
            'project_work__section',
            'project_work__section__construction_object',
            'project_work__section__construction_object__project'
        ).order_by('-year', '-month')

        # Фильтр: Проект
        project_id = self.request.GET.get('project')
        if project_id:
            qs = qs.filter(project_work__section__construction_object__project_id=project_id)

        # Фильтр: Объект
        object_id = self.request.GET.get('object')
        if object_id:
            qs = qs.filter(project_work__section__construction_object_id=object_id)

        # Фильтр: Раздел
        section_id = self.request.GET.get('section')
        if section_id:
            qs = qs.filter(project_work__section_id=section_id)

        # Фильтр: Работа
        work_id = self.request.GET.get('work')
        if work_id:
            qs = qs.filter(project_work_id=work_id)

        # Фильтр: Подработа
        item_id = self.request.GET.get('item')
        if item_id:
            qs = qs.filter(project_work__items__id=item_id)

        # Фильтр: Период (пересечение)
        # План пересекается с диапазоном, если:
        # plan.start_date <= period_end AND plan.end_date >= period_start
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        if date_from:
            qs = qs.filter(end_date__gte=date_from)
        if date_to:
            qs = qs.filter(start_date__lte=date_to)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        company = self.request.user.company

        # Списки для фильтров
        from apps.projects.models import Project, ConstructionObject, Section
        from apps.works.models import ProjectWork, ProjectWorkItem

        projects = Project.objects.filter(company=company).order_by('name')
        objects = ConstructionObject.objects.filter(company=company).order_by('name')
        sections = Section.objects.filter(company=company).order_by('name')
        works = ProjectWork.objects.filter(company=company).order_by('name')
        items = ProjectWorkItem.objects.filter(company=company).order_by('name')

        context.update({
            'projects': projects,
            'objects': objects,
            'sections': sections,
            'works': works,
            'items': items,
            # Сохраняем выбранные значения
            'selected_project': self.request.GET.get('project', ''),
            'selected_object': self.request.GET.get('object', ''),
            'selected_section': self.request.GET.get('section', ''),
            'selected_work': self.request.GET.get('work', ''),
            'selected_item': self.request.GET.get('item', ''),
            'date_from': self.request.GET.get('date_from', ''),
            'date_to': self.request.GET.get('date_to', ''),
            'filters_count': sum(1 for v in [
                self.request.GET.get('project'),
                self.request.GET.get('object'),
                self.request.GET.get('section'),
                self.request.GET.get('work'),
                self.request.GET.get('item'),
                self.request.GET.get('date_from'),
                self.request.GET.get('date_to'),
            ] if v),
        })
        return context


class PlanDetailView(CompanyScopedMixin, DetailView):
    """Детали месячного плана с матрицей дневных планов."""
    model = MonthlyPlan
    template_name = 'planning/plan_detail.html'
    context_object_name = 'plan'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plan = self.object

        # Все версии плана
        versions = plan.versions.all().order_by('-version_number')
        context['versions'] = versions

        # Определяем выбранную версию
        version_id = self.request.GET.get('version_id')
        if version_id:
            try:
                selected_version = versions.get(pk=version_id)
            except PlanVersion.DoesNotExist:
                selected_version = versions.first()
        else:
            selected_version = versions.first()

        context['version'] = selected_version

        if not selected_version:
            context['matrix_rows'] = []
            context['matrix_dates'] = []
            context['daily_plans'] = []
            return context

        # Права пользователя для смены статуса
        user = self.request.user
        is_manager = user.is_manager() or user.is_admin()

        context['can_submit'] = (
                selected_version.status == 'DRAFT'
        )
        context['can_approve'] = (
                selected_version.status == 'SUBMITTED' and is_manager
        )
        context['can_reject'] = (
                selected_version.status == 'SUBMITTED' and is_manager
        )
        context['can_complete'] = (
                selected_version.status == 'APPROVED' and is_manager
        )
        context['can_resubmit'] = (
                selected_version.status == 'REJECTED'
        )

        # Получаем дневные планы выбранной версии
        daily_plans = DailyPlan.objects.filter(
            company=self.request.user.company,
            plan_version=selected_version
        ).select_related(
            'work_item', 'work_item__project_work'
        ).order_by('date', 'work_item__project_work__name', 'work_item__name')

        if not daily_plans.exists():
            context['matrix_rows'] = []
            context['matrix_dates'] = []
            context['daily_plans'] = []
            return context

        # Собираем уникальные даты
        dates_set = sorted(set(dp.date for dp in daily_plans))

        # Группируем по работам и подработкам
        work_groups = {}
        for dp in daily_plans:
            pw = dp.work_item.project_work
            wi = dp.work_item

            pw_key = pw.pk
            if pw_key not in work_groups:
                work_groups[pw_key] = {
                    'project_work': pw,
                    'work_items': {},
                    'total_quantity': 0,
                    'total_value': 0,
                }

            wi_key = wi.pk
            if wi_key not in work_groups[pw_key]['work_items']:
                work_groups[pw_key]['work_items'][wi_key] = {
                    'work_item': wi,
                    'plans_by_date': {},
                    'total_quantity': 0,
                    'total_value': 0,
                }

            work_groups[pw_key]['work_items'][wi_key]['plans_by_date'][dp.date.isoformat()] = dp
            work_groups[pw_key]['work_items'][wi_key]['total_quantity'] += dp.planned_quantity or 0
            work_groups[pw_key]['work_items'][wi_key]['total_value'] += dp.planned_value or 0
            work_groups[pw_key]['total_quantity'] += dp.planned_quantity or 0
            work_groups[pw_key]['total_value'] += dp.planned_value or 0

        # Сортируем
        sorted_work_groups = sorted(
            work_groups.values(),
            key=lambda x: x['project_work'].name if x['project_work'] else ''
        )
        for wg in sorted_work_groups:
            wg['sorted_work_items'] = sorted(
                wg['work_items'].values(),
                key=lambda x: x['work_item'].name if x['work_item'] else ''
            )

        # Итоги по датам
        date_totals = {}
        for date in dates_set:
            date_totals[date.isoformat()] = {'quantity': 0, 'value': 0}

        for wg in sorted_work_groups:
            for wi_data in wg['work_items'].values():
                for date in dates_set:
                    dp = wi_data['plans_by_date'].get(date.isoformat())
                    if dp:
                        date_totals[date.isoformat()]['quantity'] += dp.planned_quantity or 0
                        date_totals[date.isoformat()]['value'] += dp.planned_value or 0

        # Дни недели
        weekday_names = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}

        # Информация о датах
        date_info = []
        for date in dates_set:
            first_dp = daily_plans.filter(date=date).first()
            work_day = first_dp.workday_number if first_dp else dates_set.index(date) + 1
            date_info.append({
                'date': date,
                'weekday': weekday_names.get(date.weekday(), ''),
                'work_day': work_day,
                'iso': date.isoformat(),
            })

        # Общий итог
        grand_total_quantity = sum(wg['total_quantity'] for wg in sorted_work_groups)
        grand_total_value = sum(wg['total_value'] for wg in sorted_work_groups)

        context['matrix_rows'] = sorted_work_groups
        context['matrix_dates'] = date_info
        context['date_totals'] = date_totals
        context['grand_total_quantity'] = grand_total_quantity
        context['grand_total_value'] = grand_total_value
        context['daily_plans'] = daily_plans

        return context


class MonthlyPlanCreateView(CompanyRequiredMixin, CreateView):
    model = MonthlyPlan
    form_class = MonthlyPlanForm
    template_name = 'planning/monthly_plan_form.html'
    success_url = reverse_lazy('planning:plan_list')

    def form_valid(self, form):
        # 1. Сохраняем план
        self.object = form.save(commit=False)
        self.object.company = self.get_company()

        work = self.object.project_work
        self.object.planned_value = self.object.planned_quantity * work.unit_price
        self.object.save()

        # 2. ВСЕГДА создаём версию DRAFT (даже если генерация упадёт)
        version = PlanVersion.objects.create(
            monthly_plan=self.object,
            company=self.object.company,
            version_number=1,
            status='DRAFT',
            created_by=self.request.user
        )

        # 3. Пытаемся сгенерировать дневные планы
        try:
            count = PlanGeneratorService.generate(version)
            messages.success(
                self.request,
                f'План создан. Версия v1 (DRAFT). Сгенерировано {count} дневных записей.'
            )
        except Exception as e:
            messages.warning(
                self.request,
                f'План создан (версия v1 DRAFT), но генерация дневных планов не удалась: {e}. '
                f'Вы можете сгенерировать план вручную на странице версии.'
            )

        return redirect(self.get_success_url())




class MonthlyPlanUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование месячного плана."""
    model = MonthlyPlan
    form_class = MonthlyPlanForm
    template_name = 'planning/monthly_plan_form.html'
    success_url = reverse_lazy('planning:plan_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['is_edit'] = True
        return context

    def form_valid(self, form):
        messages.success(self.request, 'План успешно обновлён!')
        return super().form_valid(form)


class MonthlyPlanDeleteView(CompanyScopedMixin, DeleteView):
    """Удаление месячного плана."""
    model = MonthlyPlan
    template_name = 'planning/plan_confirm_delete.html'
    success_url = reverse_lazy('planning:plan_list')

    def delete(self, request, *args, **kwargs):
        plan = self.get_object()
        messages.success(request, f'План "{plan.project_work.name}" за {plan.month}/{plan.year} удалён')
        return super().delete(request, *args, **kwargs)

# =============================================================================
# ВЕРСИИ ПЛАНОВ И WORKFLOW
# =============================================================================

class PlanVersionDetailView(CompanyScopedMixin, DetailView):
    model = PlanVersion
    template_name = 'planning/version_detail.html'
    context_object_name = 'version'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        v = self.object

        # Добавляем список профилей для формы
        from apps.planning.models import LoadProfile
        context['profiles'] = LoadProfile.objects.filter(company=v.company)

        daily_plans = v.daily_plans.select_related(
            'work_item', 'work_item__project_work'
        ).order_by('date', 'work_item__sequence')

        # Аннотируем фактом
        facts_map = {}
        for fact in DailyFact.objects.filter(
            company=self.request.user.company,
            project_work=v.monthly_plan.project_work,
            date__range=[v.monthly_plan.start_date, v.monthly_plan.end_date]
        ).values('work_item_id', 'date', 'actual_quantity'):
            key = (fact['work_item_id'], fact['date'])
            facts_map[key] = fact['actual_quantity']

        for dp in daily_plans:
            dp.fact_quantity = facts_map.get((dp.work_item_id, dp.date))

        context['daily_plans'] = daily_plans
        context['total_planned_quantity'] = sum(dp.planned_quantity for dp in daily_plans)
        context['total_planned_value'] = sum(dp.planned_value for dp in daily_plans)
        context['total_fact'] = sum((dp.fact_quantity or 0) for dp in daily_plans)
        context['total_deviation'] = context['total_fact'] - context['total_planned_quantity']

        return context


class PlanVersionsListView(CompanyScopedMixin, ListView):
    """Список всех версий плана."""
    model = PlanVersion
    template_name = 'planning/plan_versions_list.html'
    context_object_name = 'versions'

    def dispatch(self, request, *args, **kwargs):
        self.plan = get_object_or_404(MonthlyPlan, company=self.request.user.company, pk=kwargs['plan_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return PlanVersion.objects.filter(
            company=self.request.user.company,
            monthly_plan=self.plan
        ).order_by('-version_number')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['plan'] = self.plan
        return context


from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect


@csrf_protect
@require_POST
def daily_plan_inline_update(request):
    """Inline обновление дневного плана через AJAX."""
    try:
        import json
        data = json.loads(request.body)
        plan_id = data.get('plan_id')
        field = data.get('field')
        value = data.get('value')

        plan = DailyPlan.objects.get(company=request.user.company, pk=plan_id)

        # Проверяем, что версия не immutable
        if plan.plan_version.is_immutable:
            return JsonResponse({
                'success': False,
                'message': 'Версия утверждена и не может быть изменена'
            }, status=403)

        if field == 'planned_quantity':
            from decimal import Decimal, InvalidOperation
            try:
                qty = Decimal(str(value)) if value else Decimal('0')
            except (InvalidOperation, ValueError):
                return JsonResponse({'success': False, 'message': 'Некорректное число'}, status=400)

            plan.planned_quantity = qty

            # Пересчитываем стоимость
            unit_price = plan.work_item.project_work.unit_price
            if unit_price is None:
                unit_price = Decimal('0')
            plan.planned_value = qty * unit_price

        elif field == 'planned_value':
            from decimal import Decimal, InvalidOperation
            try:
                plan.planned_value = Decimal(str(value)) if value else Decimal('0')
            except (InvalidOperation, ValueError):
                return JsonResponse({'success': False, 'message': 'Некорректное число'}, status=400)
        else:
            return JsonResponse({'success': False, 'message': f'Неизвестное поле: {field}'}, status=400)

        plan.save()

        return JsonResponse({
            'success': True,
            'planned_quantity': float(plan.planned_quantity),
            'planned_value': float(plan.planned_value),
        })
    except DailyPlan.DoesNotExist:
        return JsonResponse({'success': False, 'message': 'Не найдено'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=400)


class PlanMatrixView(View):
    """Матричное отображение дневных планов по иерархии: Проект → Объект → Раздел → Работа → Подработа."""
    template_name = 'planning/plan_matrix.html'

    def get(self, request):
        # Получаем фильтры
        project_id = request.GET.get('project')
        object_id = request.GET.get('object')
        section_id = request.GET.get('section')
        date_from = request.GET.get('date_from')
        date_to = request.GET.get('date_to')

        # Базовый queryset — только утверждённые версии
        daily_plans = DailyPlan.objects.filter(
            company=self.request.user.company,
            plan_version__status='APPROVED'
        ).select_related(
            'work_item',
            'work_item__project_work',
            'work_item__project_work__section',
            'work_item__project_work__section__construction_object',
            'work_item__project_work__section__construction_object__project',
            'plan_version',
            'plan_version__monthly_plan'
        ).order_by(
            'work_item__project_work__section__construction_object__project__name',
            'work_item__project_work__section__construction_object__name',
            'work_item__project_work__section__name',
            'work_item__project_work__name',
            'work_item__name',
            'date'
        )

        # Применяем фильтры
        if project_id:
            daily_plans = daily_plans.filter(
                work_item__project_work__section__construction_object__project_id=project_id
            )
        if object_id:
            daily_plans = daily_plans.filter(
                work_item__project_work__section__construction_object_id=object_id
            )
        if section_id:
            daily_plans = daily_plans.filter(
                work_item__project_work__section_id=section_id
            )
        if date_from:
            daily_plans = daily_plans.filter(date__gte=date_from)
        if date_to:
            daily_plans = daily_plans.filter(date__lte=date_to)

        # Собираем уникальные даты
        dates_set = sorted(set(dp.date for dp in daily_plans))

        # Группируем по иерархии
        hierarchy = {}
        for dp in daily_plans:
            work = dp.work_item.project_work
            section = work.section
            obj = section.construction_object
            project = obj.project

            proj_key = project.pk
            if proj_key not in hierarchy:
                hierarchy[proj_key] = {
                    'project': project,
                    'objects': {},
                    'total_by_date': {d.isoformat(): Decimal('0') for d in dates_set},
                    'grand_total': Decimal('0'),
                }

            obj_key = obj.pk
            if obj_key not in hierarchy[proj_key]['objects']:
                hierarchy[proj_key]['objects'][obj_key] = {
                    'object': obj,
                    'sections': {},
                    'total_by_date': {d.isoformat(): Decimal('0') for d in dates_set},
                    'grand_total': Decimal('0'),
                }

            sec_key = section.pk
            if sec_key not in hierarchy[proj_key]['objects'][obj_key]['sections']:
                hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key] = {
                    'section': section,
                    'works': {},
                    'total_by_date': {d.isoformat(): Decimal('0') for d in dates_set},
                    'grand_total': Decimal('0'),
                }

            work_key = work.pk
            if work_key not in hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]['works']:
                hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]['works'][work_key] = {
                    'work': work,
                    'items': {},
                    'total_by_date': {d.isoformat(): Decimal('0') for d in dates_set},
                    'grand_total': Decimal('0'),
                }

            item_key = dp.work_item.pk
            if item_key not in hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]['works'][work_key]['items']:
                hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]['works'][work_key]['items'][item_key] = {
                    'item': dp.work_item,
                    'plans_by_date': {},
                    'total_by_date': {d.isoformat(): Decimal('0') for d in dates_set},
                    'grand_total': Decimal('0'),
                }

            # Добавляем план
            date_str = dp.date.isoformat()
            item_data = hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]['works'][work_key]['items'][item_key]
            item_data['plans_by_date'][date_str] = dp
            item_data['total_by_date'][date_str] += dp.planned_quantity or 0
            item_data['grand_total'] += dp.planned_quantity or 0

            # Суммируем на уровень выше
            work_data = hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]['works'][work_key]
            work_data['total_by_date'][date_str] += dp.planned_quantity or 0
            work_data['grand_total'] += dp.planned_quantity or 0

            sec_data = hierarchy[proj_key]['objects'][obj_key]['sections'][sec_key]
            sec_data['total_by_date'][date_str] += dp.planned_quantity or 0
            sec_data['grand_total'] += dp.planned_quantity or 0

            obj_data = hierarchy[proj_key]['objects'][obj_key]
            obj_data['total_by_date'][date_str] += dp.planned_quantity or 0
            obj_data['grand_total'] += dp.planned_quantity or 0

            proj_data = hierarchy[proj_key]
            proj_data['total_by_date'][date_str] += dp.planned_quantity or 0
            proj_data['grand_total'] += dp.planned_quantity or 0

        # Сортируем иерархию
        sorted_hierarchy = sorted(hierarchy.values(), key=lambda x: x['project'].name)
        for proj in sorted_hierarchy:
            proj['objects'] = sorted(proj['objects'].values(), key=lambda x: x['object'].name)
            for obj in proj['objects']:
                obj['sections'] = sorted(obj['sections'].values(), key=lambda x: x['section'].name)
                for sec in obj['sections']:
                    sec['works'] = sorted(sec['works'].values(), key=lambda x: x['work'].name)
                    for work in sec['works']:
                        work['items'] = sorted(work['items'].values(), key=lambda x: x['item'].name)

        # Получаем списки для фильтров
        from apps.projects.models import Project, ConstructionObject, Section
        projects = Project.objects.filter(company=request.user.company).order_by('name')
        objects = ConstructionObject.objects.filter(company=request.user.company).order_by('name')
        sections = Section.objects.filter(company=request.user.company).order_by('name')

        # Дни недели
        weekday_names = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
        date_info = []
        for date in dates_set:
            date_info.append({
                'date': date,
                'weekday': weekday_names.get(date.weekday(), ''),
                'iso': date.isoformat(),
            })

        return render(request, self.template_name, {
            'hierarchy': sorted_hierarchy,
            'dates': date_info,
            'projects': projects,
            'objects': objects,
            'sections': sections,
            'selected_project': project_id,
            'selected_object': object_id,
            'selected_section': section_id,
            'date_from': date_from or '',
            'date_to': date_to or '',
            'total_records': daily_plans.count(),
        })

# Keep URL imports compatible while separating the planning components.
from .profile_views import (
    LoadProfileCreateView,
    LoadProfileDeleteView,
    LoadProfileDetailView,
    LoadProfileItemCreateView,
    LoadProfileItemDeleteView,
    LoadProfileItemUpdateView,
    LoadProfileListView,
    LoadProfileUpdateView,
)
from .calendar_views import (
    CalendarAutoFillView,
    CalendarCreateView,
    CalendarDayBulkEditView,
    CalendarDayUpdateView,
    CalendarDeleteView,
    CalendarDetailView,
    CalendarListView,
    CalendarUpdateView,
)
from .workflow_views import (
    SetBaselineVersionView,
    VersionApproveView,
    VersionCompleteView,
    VersionCreateView,
    VersionGenerateView,
    VersionRegenerateView,
    VersionRejectView,
    VersionRevisionView,
    VersionSubmitView,
)
