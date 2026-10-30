from django.shortcuts import render, redirect
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
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
            if user.auth_source=='LDAP' and (not user.company_id or not user.role_id):
                return redirect('accounts:access_pending')
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

    def list_mode(self):
        mode=self.request.GET.get('view',self.request.session.get('user_list_view','table'))
        return mode if mode in {'table','tree'} else 'table'

    def get_paginate_by(self,queryset):
        return None if self.list_mode()=='tree' else self.paginate_by

    def get_queryset(self):
        from django.db.models import Q
        from .forms import UserFilterForm
        queryset = super().get_queryset().select_related('company', 'role', 'access_group__role')
        self.filter_form = UserFilterForm(self.request.GET, company=self.request.user.company)
        self.archive_mode = self.request.GET.get('archive') == 'yes'
        queryset = queryset.filter(archived_at__isnull=not self.archive_mode)
        self.total_count = queryset.count()
        if not self.filter_form.is_valid():
            return queryset.none()
        data = self.filter_form.cleaned_data
        for token in data['q'].split():
            queryset = queryset.filter(Q(username__icontains=token) | Q(ldap_login__icontains=token) | Q(first_name__icontains=token) | Q(last_name__icontains=token) | Q(email__icontains=token) | Q(position__icontains=token))
        for field in ['role', 'access_group', 'auth_source']:
            if data.get(field):
                queryset = queryset.filter(**{field: data[field]})
        if data['active']:
            queryset = queryset.filter(is_active=data['active'] == 'yes')
        if data['unassigned']:
            queryset = queryset.filter(role__isnull=True, is_superuser=False)
        from django.db.models import OuterRef,Subquery,UUIDField
        from .invitation_models import UserInvitation
        invitations=UserInvitation.objects.filter(recipient_id=OuterRef('pk')).order_by('-created_at','-pk')
        queryset=queryset.annotate(latest_invitation_batch=Subquery(invitations.values('batch_id')[:1],output_field=UUIDField()),latest_invitation_status=Subquery(invitations.values('status')[:1]))
        return queryset.order_by('last_name', 'first_name', 'username', 'pk')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop('page', None)
        context.update(filter_form=self.filter_form, total_count=self.total_count, filter_query=params.urlencode(), archive_mode=self.archive_mode)
        mode=self.list_mode();self.request.session['user_list_view']=mode
        groups={}
        if mode=='tree':
            for user in context['users']:
                group=groups.setdefault(user.access_group_id,{'group':user.access_group,'users':[]})
                group['users'].append(user)
        context['user_groups']=sorted(groups.values(),key=lambda row:(row['group'] is None,row['group'].name.casefold() if row['group'] else ''))
        context['view_mode']=mode
        context['matched_count']=context['paginator'].count if context.get('paginator') else len(context['users'])
        for view in ['table','tree']:
            query=params.copy();query['view']=view
            context[view+'_view_query']=query.urlencode()

        return context


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


@login_required
@sensitive_post_parameters('old_password', 'new_password1', 'new_password2')
@never_cache
@require_http_methods(['GET', 'POST'])
def password_change_view(request):
    if request.user.auth_source=='LDAP':
        messages.info(request, 'Пароль корпоративной учетной записи меняется в Windows или через администратора AD.')
        return redirect('accounts:access_pending' if not request.user.company_id or not request.user.role_id else 'accounts:settings')
    form = PasswordChangeForm(request.user, request.POST if request.method == 'POST' else None)
    for field in form.fields.values():
        field.widget.attrs['class'] = 'form-control'
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, 'Пароль успешно изменен.')
        return redirect('accounts:password_change')
    return render(request, 'accounts/password_change.html', {'form': form})


@login_required
def access_pending_view(request):
    return render(request,'accounts/access_pending.html')
