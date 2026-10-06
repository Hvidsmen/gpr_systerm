import datetime
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, View
from django.urls import reverse_lazy
from django.db.models import Count, Q
from core.mixins import CompanyRequiredMixin, CompanyScopedMixin
from .models import ProductionCalendar, CalendarDay
from .forms import ProductionCalendarForm, CalendarDayForm
from .services import CalendarAutoFillService


class CalendarListView(CompanyScopedMixin, ListView):
    """Список производственных календарей."""
    model = ProductionCalendar
    template_name = 'planning/calendar_list.html'
    context_object_name = 'calendars'

    def get_queryset(self):
        return ProductionCalendar.objects.filter(company=self.request.user.company).annotate(
            days_count=Count('days'),
            working_days_count=Count('days', filter=Q(days__is_working=True))
        ).order_by('-year', 'name')


class CalendarDetailView(CompanyScopedMixin, DetailView):
    """Детали календаря с днями, сгруппированными по месяцам."""
    model = ProductionCalendar
    template_name = 'planning/calendar_detail.html'
    context_object_name = 'calendar'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        calendar = self.object

        days = CalendarDay.objects.filter(company=self.request.user.company, calendar=calendar).order_by('date')
        context['days'] = days
        context['total_days'] = days.count()
        context['working_days'] = days.filter(is_working=True).count()
        context['holidays'] = days.filter(is_holiday=True).count()
        context['weekends'] = days.filter(is_working=False, is_holiday=False).count()
        context['shortened'] = days.filter(is_shortened=True).count()

        # Группировка по месяцам
        months_data = {}
        for day in days:
            month_key = day.date.month
            if month_key not in months_data:
                months_data[month_key] = {
                    'name': day.date.strftime('%B'),
                    'days': [],
                    'working': 0,
                    'total': 0
                }
            months_data[month_key]['days'].append(day)
            months_data[month_key]['total'] += 1
            if day.is_working:
                months_data[month_key]['working'] += 1

        context['months_data'] = months_data
        return context


class CalendarCreateView(CompanyRequiredMixin, CreateView):
    """Создание производственного календаря."""
    model = ProductionCalendar
    form_class = ProductionCalendarForm
    template_name = 'planning/calendar_form.html'
    success_url = reverse_lazy('planning:calendar_list')

    def form_valid(self, form):
        messages.success(self.request, 'Календарь создан. Теперь заполните его днями.')
        return super().form_valid(form)


class CalendarUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование календаря."""
    model = ProductionCalendar
    form_class = ProductionCalendarForm
    template_name = 'planning/calendar_form.html'
    success_url = reverse_lazy('planning:calendar_list')

    def form_valid(self, form):
        messages.success(self.request, 'Календарь обновлён!')
        return super().form_valid(form)


class CalendarDeleteView(CompanyScopedMixin, DeleteView):
    """Удаление календаря."""
    model = ProductionCalendar
    template_name = 'planning/calendar_confirm_delete.html'
    success_url = reverse_lazy('planning:calendar_list')

    def delete(self, request, *args, **kwargs):
        calendar = self.get_object()
        messages.success(request, f'Календарь "{calendar.name}" удалён')
        return super().delete(request, *args, **kwargs)


class CalendarAutoFillView(View):
    """Автоматическое заполнение календаря днями года."""

    def post(self, request, pk):
        calendar = get_object_or_404(ProductionCalendar, company=self.request.user.company, pk=pk)

        if calendar.days.exists():
            messages.warning(
                request,
                f'Календарь уже содержит {calendar.days.count()} дней. '
                f'Они будут заменены.'
            )

        try:
            count = CalendarAutoFillService.generate_year(calendar, calendar.year)
            working_count = CalendarDay.objects.filter(
                company=self.request.user.company,
                calendar=calendar, is_working=True
            ).count()
            messages.success(
                request,
                f'Календарь заполнен: создано {count} дней, '
                f'из них рабочих — {working_count}'
            )
        except Exception as e:
            messages.error(request, f'Ошибка заполнения: {e}')

        return redirect('planning:calendar_detail', pk=pk)


class CalendarDayUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование отдельного дня календаря."""
    model = CalendarDay
    form_class = CalendarDayForm
    template_name = 'planning/calendar_day_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['calendar'] = self.object.calendar
        return context

    def form_valid(self, form):
        messages.success(
            self.request,
            f'День {self.object.date.strftime("%d.%m.%Y")} обновлён'
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('planning:calendar_detail', kwargs={'pk': self.object.calendar_id})


class CalendarDayBulkEditView(View):
    """Массовое редактирование дней календаря (диапазон дат)."""

    def post(self, request, pk):
        calendar = get_object_or_404(ProductionCalendar, company=self.request.user.company, pk=pk)

        start_date = request.POST.get('start_date')
        end_date = request.POST.get('end_date')
        action = request.POST.get('action')

        if not start_date or not end_date:
            messages.error(request, 'Укажите даты начала и окончания')
            return redirect('planning:calendar_detail', pk=pk)

        try:
            start = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
            end = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, 'Неверный формат даты')
            return redirect('planning:calendar_detail', pk=pk)

        days = CalendarDay.objects.filter(
            company=self.request.user.company,
            calendar=calendar,
            date__gte=start,
            date__lte=end
        )

        count = days.count()
        if count == 0:
            messages.warning(request, 'В указанном диапазоне нет дней')
            return redirect('planning:calendar_detail', pk=pk)

        if action == 'make_working':
            days.update(is_working=True, is_holiday=False, note='Сделано рабочим (массово)')
        elif action == 'make_holiday':
            days.update(is_working=False, is_holiday=True, note='Праздник (массово)')
        elif action == 'make_weekend':
            days.update(is_working=False, is_holiday=False, note='Выходной (массово)')
        else:
            messages.error(request, 'Неизвестное действие')
            return redirect('planning:calendar_detail', pk=pk)

        messages.success(request, f'Обновлено {count} дней')
        return redirect('planning:calendar_detail', pk=pk)
