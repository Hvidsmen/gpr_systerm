"""
Ввод факта работ и подработ с фильтрацией.
"""
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views import View
from django.db.models import Sum
from decimal import Decimal

from apps.production.forms import FactInputFilterForm
from apps.planning.models import DailyPlan, PlanVersion
from apps.production.models import DailyFact, DeviationReason
from apps.works.models import ProjectWork
from apps.projects.models import Project, ConstructionObject, Section


class FactInputView(View):
    """Ввод факта работ и подработ с фильтрацией по проекту/объекту/разделу."""
    template_name = 'production/fact_input.html'

    def get(self, request):
        form = FactInputFilterForm(user=request.user)
        return render(request, self.template_name, {
            'form': form,
            'works_data': [],
            'selected_date': None,
            'selected_project': None,
            'selected_object': None,
            'selected_section': None,
            'deviation_reasons': DeviationReason.objects.filter(is_active=True),
        })

    def post(self, request):
        if 'select_filters' in request.POST:
            return self._show_works(request)
        elif 'save_facts' in request.POST:
            return self._save_facts(request)
        return redirect('production:fact_input')

    def _show_works(self, request):
        """Показать работы с планами на выбранную дату."""
        form = FactInputFilterForm(request.POST, user=request.user)
        if not form.is_valid():
            messages.error(request, 'Проверьте форму')
            return render(request, self.template_name, {
                'form': form, 'works_data': [],
                'selected_date': None, 'deviation_reasons': DeviationReason.objects.filter(is_active=True),
            })

        target_date = form.cleaned_data['date']
        project = form.cleaned_data.get('project')
        construction_object = form.cleaned_data.get('construction_object')
        section = form.cleaned_data.get('section')

        # Фильтруем работы
        works_qs = ProjectWork.objects.filter(company=request.user.company)
        if project:
            works_qs = works_qs.filter(section__construction_object__project=project)
        if construction_object:
            works_qs = works_qs.filter(section__construction_object=construction_object)
        if section:
            works_qs = works_qs.filter(section=section)

        # Находим планы на выбранную дату
        daily_plans = DailyPlan.objects.filter(
            date=target_date,
            work_item__project_work__in=works_qs,
            plan_version__status='APPROVED'
        ).select_related(
            'work_item', 'work_item__project_work',
            'work_item__project_work__section',
            'work_item__project_work__section__construction_object',
            'work_item__project_work__section__construction_object__project'
        ).order_by('work_item__project_work__name', 'work_item__sequence')

        # Группируем по работам
        works_data = {}
        for dp in daily_plans:
            work = dp.work_item.project_work
            if work.pk not in works_data:
                works_data[work.pk] = {
                    'work': work,
                    'items': [],
                    'total_plan': Decimal('0'),
                    'total_fact': Decimal('0'),
                }

            # Ищем существующий факт
            fact = DailyFact.objects.filter(
                project_work=work,
                work_item=dp.work_item,
                date=target_date
            ).first()

            works_data[work.pk]['items'].append({
                'item': dp.work_item,
                'plan': dp,
                'fact': fact,
                'plan_quantity': dp.planned_quantity,
                'fact_quantity': fact.actual_quantity if fact else None,
            })
            works_data[work.pk]['total_plan'] += dp.planned_quantity
            if fact:
                works_data[work.pk]['total_fact'] += fact.actual_quantity

        deviation_reasons = DeviationReason.objects.filter(is_active=True)

        return render(request, self.template_name, {
            'form': form,
            'works_data': list(works_data.values()),
            'selected_date': target_date,
            'selected_project': project,
            'selected_object': construction_object,
            'selected_section': section,
            'deviation_reasons': deviation_reasons,
            'plans_count': daily_plans.count(),
        })

    def _save_facts(self, request):
        """Сохранить введённые факты."""
        target_date = request.POST.get('date')
        if not target_date:
            messages.error(request, 'Не указана дата')
            return redirect('production:fact_input')

        plan_ids = request.POST.getlist('plan_id')
        saved_count = 0

        for plan_id in plan_ids:
            try:
                dp = DailyPlan.objects.get(pk=plan_id, company=request.user.company)
                work = dp.work_item.project_work

                fact_value = request.POST.get(f'fact_{plan_id}', '')
                reason_id = request.POST.get(f'reason_{plan_id}', '')
                comment = request.POST.get(f'comment_{plan_id}', '')

                # Пропускаем пустые значения
                if not fact_value or fact_value.strip() == '':
                    continue

                qty = Decimal(fact_value)
                reason = DeviationReason.objects.get(pk=reason_id) if reason_id else None
                actual_value = qty * work.unit_price

                DailyFact.objects.update_or_create(
                    project_work=work,
                    work_item=dp.work_item,
                    date=target_date,
                    company=request.user.company,
                    defaults={
                        'actual_quantity': qty,
                        'actual_value': actual_value,
                        'reported_by': request.user,
                        'deviation_reason': reason,
                        'comment': comment,
                    }
                )
                saved_count += 1

            except DailyPlan.DoesNotExist:
                continue
            except Exception as e:
                messages.error(request, f'Ошибка для плана #{plan_id}: {e}')

        messages.success(request, f'Сохранено фактов: {saved_count} на дату {target_date}')
        return redirect('production:fact_input')