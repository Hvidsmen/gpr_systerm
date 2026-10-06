"""
Views для дневных фактов (объёмов работ).
"""
from django.contrib import messages
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy

from core.mixins import CompanyRequiredMixin
from apps.production.models import DailyFact
from apps.production.forms import DailyFactForm


class FactListView(ListView):
    model = DailyFact
    template_name = 'production/fact_list.html'
    context_object_name = 'facts'
    paginate_by = 20

    def get_queryset(self):
        return DailyFact.objects.filter(
            company=self.request.user.company
        ).select_related(
            'project_work', 'work_item', 'reported_by', 'deviation_reason'
        ).order_by('-date', 'project_work__name')


class FactCreateView(CompanyRequiredMixin, CreateView):
    model = DailyFact
    form_class = DailyFactForm
    template_name = 'production/fact_form.html'
    success_url = reverse_lazy('production:fact_list')

    def form_valid(self, form):
        form.instance.reported_by = self.request.user
        messages.success(self.request, 'Факт по объёму успешно введён!')
        return super().form_valid(form)


class FactUpdateView(UpdateView):
    model = DailyFact
    form_class = DailyFactForm
    template_name = 'production/fact_form.html'

    def get_success_url(self):
        return reverse_lazy('production:fact_list')

    def form_valid(self, form):
        messages.success(self.request, 'Факт обновлён!')
        return super().form_valid(form)


class FactDeleteView(DeleteView):
    model = DailyFact
    template_name = 'production/resource_confirm_delete.html'
    success_url = reverse_lazy('production:fact_list')

    def delete(self, request, *args, **kwargs):
        fact = self.get_object()
        messages.success(request, f'Факт за {fact.date} удалён')
        return super().delete(request, *args, **kwargs)