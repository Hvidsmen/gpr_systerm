from django.shortcuts import get_object_or_404
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.db.models import Sum
from core.mixins import CompanyRequiredMixin, CompanyScopedMixin
from .models import LoadProfile, LoadProfileItem
from .forms import LoadProfileForm, LoadProfileItemForm


class LoadProfileListView(CompanyScopedMixin, ListView):
    """Список профилей нагрузки."""
    model = LoadProfile
    template_name = 'planning/profile_list.html'
    context_object_name = 'profiles'

    def get_queryset(self):
        return LoadProfile.objects.filter(company=self.request.user.company).annotate(
            total_percentage=Sum('items__percentage')
        )


class LoadProfileDetailView(CompanyScopedMixin, DetailView):
    """Детали профиля нагрузки с элементами."""
    model = LoadProfile
    template_name = 'planning/profile_detail.html'
    context_object_name = 'profile'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['items'] = self.object.items.order_by('workday_number')

        total = sum(item.percentage for item in context['items'])
        context['total_percentage'] = total
        context['diff_to_100'] = 100 - total
        context['is_valid'] = (total == 100)

        return context


class LoadProfileCreateView(CompanyRequiredMixin, CreateView):
    """Создание профиля нагрузки."""
    model = LoadProfile
    form_class = LoadProfileForm
    template_name = 'planning/profile_form.html'
    success_url = reverse_lazy('planning:profile_list')

    def form_valid(self, form):
        messages.success(self.request, 'Профиль нагрузки успешно создан!')
        return super().form_valid(form)


class LoadProfileUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование профиля нагрузки."""
    model = LoadProfile
    form_class = LoadProfileForm
    template_name = 'planning/profile_form.html'
    success_url = reverse_lazy('planning:profile_list')

    def form_valid(self, form):
        messages.success(self.request, 'Профиль нагрузки успешно обновлён!')
        return super().form_valid(form)


class LoadProfileDeleteView(CompanyScopedMixin, DeleteView):
    """Удаление профиля нагрузки."""
    model = LoadProfile
    template_name = 'planning/profile_confirm_delete.html'
    success_url = reverse_lazy('planning:profile_list')

    def delete(self, request, *args, **kwargs):
        profile = self.get_object()
        messages.success(request, f'Профиль "{profile.name}" удалён')
        return super().delete(request, *args, **kwargs)


class LoadProfileItemCreateView(CompanyScopedMixin, CreateView):
    """Добавление элемента (рабочего дня) в профиль."""
    model = LoadProfileItem
    form_class = LoadProfileItemForm
    template_name = 'planning/profile_item_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile'] = get_object_or_404(LoadProfile, company=self.request.user.company, pk=self.kwargs['profile_pk'])
        context['is_edit'] = False
        return context

    def form_valid(self, form):
        profile = get_object_or_404(LoadProfile, company=self.request.user.company, pk=self.kwargs['profile_pk'])
        form.instance.profile = profile
        form.instance.company = profile.company
        messages.success(
            self.request,
            f'Элемент профиля добавлен: день {form.instance.workday_number} — {form.instance.percentage}%'
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('planning:profile_detail', kwargs={'pk': self.kwargs['profile_pk']})


class LoadProfileItemUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование элемента профиля."""
    model = LoadProfileItem
    form_class = LoadProfileItemForm
    template_name = 'planning/profile_item_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['profile'] = self.object.profile
        context['is_edit'] = True
        return context

    def form_valid(self, form):
        messages.success(
            self.request,
            f'Элемент профиля обновлён: день {form.instance.workday_number} — {form.instance.percentage}%'
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('planning:profile_detail', kwargs={'pk': self.object.profile_id})


class LoadProfileItemDeleteView(CompanyScopedMixin, DeleteView):
    """Удаление элемента профиля."""
    model = LoadProfileItem
    template_name = 'planning/profile_item_confirm_delete.html'

    def get_success_url(self):
        return reverse_lazy('planning:profile_detail', kwargs={'pk': self.object.profile_id})

    def delete(self, request, *args, **kwargs):
        item = self.get_object()
        messages.success(request, f'Элемент "День {item.workday_number}" удалён')
        return super().delete(request, *args, **kwargs)
