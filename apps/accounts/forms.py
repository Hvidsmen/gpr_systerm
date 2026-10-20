from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.utils.translation import gettext_lazy as _

from .models import User, Role, AccessGroup


class UserSettingsForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('last_name', 'first_name', 'email', 'phone')
        labels = {'last_name': 'Фамилия', 'first_name': 'Имя', 'email': 'Email', 'phone': 'Телефон'}
        widgets = {
            name: forms.TextInput(attrs={'class': 'form-control'})
            for name in ('last_name', 'first_name', 'phone')
        }
        widgets['email'] = forms.EmailInput(attrs={'class': 'form-control'})


class UserRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        label=_('Email'),
        widget=forms.EmailInput(attrs={'class': 'form-control'})
    )
    first_name = forms.CharField(
        required=True,
        label=_('Имя'),
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )
    last_name = forms.CharField(
        required=True,
        label=_('Фамилия'),
        widget=forms.TextInput(attrs={'class': 'form-control'})
    )

    class Meta:
        model = User
        fields = ('username', 'email', 'first_name', 'last_name', 'password1', 'password2')
        widgets = {
            'username': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['password1'].widget.attrs.update({'class': 'form-control'})
        self.fields['password2'].widget.attrs.update({'class': 'form-control'})


class UserLoginForm(AuthenticationForm):
    auth_source = forms.ChoiceField(label='Способ входа', choices=[('local','Локальная учетная запись')], widget=forms.Select(attrs={'class':'form-select'}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from django.conf import settings
        if settings.LDAP_ENABLED:
            self.fields['auth_source'].choices = [
                ('local', 'Локальная учетная запись'),
                ('ldap', 'Корпоративная учетная запись AD'),
            ]
        else:
            self.fields['auth_source'].widget=forms.HiddenInput()
            self.fields['auth_source'].required=False

    username = forms.CharField(
        label=_('Имя пользователя'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'autofocus': True})
    )
    password = forms.CharField(
        label=_('Пароль'),
        strip=False,
        widget=forms.PasswordInput(attrs={'class': 'form-control'})
    )


class CompanyUserAccessForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone', 'position', 'access_group', 'role', 'assigned_objects', 'is_active')

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        from core.permissions import ROLES
        from apps.projects.models import ConstructionObject
        self.instance.company = company
        self.fields['access_group'].queryset = self.fields['access_group'].queryset.filter(company=company)
        self.fields['access_group'].help_text = 'Роль и объекты наследуются от группы. Для индивидуальной настройки оставьте пустым.'
        self.fields['role'].required = False
        self.fields['role'].queryset = self.fields['role'].queryset.filter(code__in=ROLES)
        self.fields['assigned_objects'].queryset = ConstructionObject.objects.filter(company=company)
        self.fields['assigned_objects'].help_text = 'Мастер и диспетчер видят и вводят факты только на этих объектах. Диспетчеру ежедневно создаются задания за вчера по каждому назначенному объекту.'
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-control'

    def clean(self):
        data = super().clean()
        self.instance.access_group_from_ad = False
        group = data.get('access_group')
        if group:
            if self.instance.is_superuser or self.instance.is_staff or (self.instance.role_id and self.instance.role.code == 'ADMIN'):
                self.add_error('access_group', 'Администратор управляется индивидуально.')
            data['role'] = group.role
            data['assigned_objects'] = group.assigned_objects.all()
            self.instance.access_group_from_ad = False
        elif not data.get('role'):
            self.add_error('role', 'Выберите роль или группу доступа.')
        return data


class CompanyUserCreateForm(CompanyUserAccessForm, UserCreationForm):
    class Meta(CompanyUserAccessForm.Meta):
        fields = ('username', *CompanyUserAccessForm.Meta.fields, 'password1', 'password2')


class UserFilterForm(forms.Form):
    q = forms.CharField(label='Поиск', required=False, max_length=200, widget=forms.TextInput(attrs={'placeholder': 'ФИО, логин, email или должность'}))
    role = forms.ModelChoiceField(label='Роль', queryset=Role.objects.none(), required=False, empty_label='Все роли')
    access_group = forms.ModelChoiceField(label='Группа доступа', queryset=AccessGroup.objects.none(), required=False, empty_label='Все группы')
    auth_source = forms.ChoiceField(label='Источник входа', required=False, choices=[('', 'Все'), ('LOCAL', 'Локальный'), ('LDAP', 'Active Directory')])
    active = forms.ChoiceField(label='Активность', required=False, choices=[('', 'Все'), ('yes', 'Активные'), ('no', 'Отключённые')])
    unassigned = forms.BooleanField(label='Без назначенной роли', required=False)

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['role'].queryset = Role.objects.all()
        self.fields['access_group'].queryset = AccessGroup.objects.filter(company=company)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-check-input' if isinstance(field.widget, forms.CheckboxInput) else 'form-control'
