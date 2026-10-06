from django.shortcuts import render, redirect
from django.contrib.auth import login, logout
from django.contrib import messages
from django.views.generic import ListView, DetailView
from django.utils.translation import gettext_lazy as _

from .models import User
from .forms import UserRegistrationForm, UserLoginForm


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
            return redirect(next_url)
    else:
        form = UserLoginForm()
    return render(request, 'accounts/login.html', {'form': form})


def logout_view(request):
    logout(request)
    messages.info(request, _('Вы вышли из системы.'))
    return redirect('accounts:login')


class UserListView(ListView):
    model = User
    template_name = 'accounts/user_list.html'
    context_object_name = 'users'
    paginate_by = 20


class UserDetailView(DetailView):
    model = User
    template_name = 'accounts/user_detail.html'
    context_object_name = 'user'


from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages


@login_required
def user_settings_view(request):
    """Страница настроек пользователя."""
    if request.method == 'POST':
        user = request.user
        user.full_name = request.POST.get('full_name', user.full_name)
        user.email = request.POST.get('email', user.email)
        user.phone = request.POST.get('phone', user.phone)
        user.save()
        messages.success(request, 'Настройки сохранены!')
        return redirect('accounts:settings')

    return render(request, 'accounts/settings.html')