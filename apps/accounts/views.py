from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib import messages
from django.views.generic import ListView, DetailView
from django.utils.translation import gettext_lazy as _
from django.utils.http import url_has_allowed_host_and_scheme

from .models import User
from .forms import UserRegistrationForm, UserLoginForm, UserSettingsForm


def register_view(request):
    if request.method == 'POST':
        form = UserRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, _('Регистрация успешна!'))
            return redirect('dashboard:index')
    else:
        form = UserRegistrationForm()
    return render(request, 'accounts/register.html', {'form': form})


def login_view(request):
    if request.method == 'POST':
        form = UserLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, _('Вы успешно вошли в систему!'))
            next_url = request.GET.get('next', 'dashboard:index')
            if not url_has_allowed_host_and_scheme(
                next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            ):
                next_url = 'dashboard:index'
            return redirect(next_url)
    else:
        form = UserLoginForm()
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, _('Вы вышли из системы.'))
    return redirect('accounts:login')


from core.mixins import CompanyScopedMixin

class UserListView(CompanyScopedMixin, ListView):
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    paginate_by = 20


class UserDetailView(CompanyScopedMixin, DetailView):
    model = User
    template_name = 'accounts/user_detail.html'
    context_object_name = 'profile_user'


from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages


@login_required
def user_settings_view(request):
    """Страница настроек пользователя."""
    form = UserSettingsForm(request.POST if request.method == 'POST' else None, instance=request.user)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Настройки сохранены!')
        return redirect('accounts:settings')

    return render(request, 'accounts/settings.html', {'form': form})


from django.views.generic import CreateView, UpdateView
from django.urls import reverse_lazy
from .forms import CompanyUserAccessForm, CompanyUserCreateForm


class CompanyUserCreateView(CreateView):
    model = User
    form_class = CompanyUserCreateForm
    template_name = 'accounts/user_access_form.html'
    success_url = reverse_lazy('accounts:user_list')

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'company': self.request.user.company}


class CompanyUserAccessView(CompanyScopedMixin, UpdateView):
    model = User
    form_class = CompanyUserAccessForm
    template_name = 'accounts/user_access_form.html'
    success_url = reverse_lazy('accounts:user_list')

    def get_queryset(self):
        # Global administrators are managed only through the global admin panel.
        return super().get_queryset().filter(is_superuser=False)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), 'company': self.request.user.company}

    def form_valid(self, form):
        if self.object.pk == self.request.user.pk and (
            form.cleaned_data['role'].code != 'ADMIN' or not form.cleaned_data['is_active']
        ):
            form.add_error(None, 'Нельзя снять собственные права администратора или отключить свою учётную запись.')
            return self.form_invalid(form)
        return super().form_valid(form)
