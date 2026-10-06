from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.utils.translation import gettext_lazy as _

from .models import User


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
    username = forms.CharField(
        label=_('Имя пользователя'),
        widget=forms.TextInput(attrs={'class': 'form-control', 'autofocus': True})
    )
    password = forms.CharField(
        label=_('Пароль'),
        widget=forms.PasswordInput(attrs={'class': 'form-control'})
    )


class CompanyUserAccessForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'phone', 'position', 'role', 'assigned_objects', 'is_active')

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        from core.permissions import ROLES
        from apps.projects.models import ConstructionObject
        self.instance.company = company
        self.fields['role'].required = True
        self.fields['role'].queryset = self.fields['role'].queryset.filter(code__in=ROLES)
        self.fields['assigned_objects'].queryset = ConstructionObject.objects.filter(company=company)
        self.fields['assigned_objects'].help_text = 'Мастер видит и вводит факты только на этих объектах. Без назначения доступных объектов нет.'
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs['class'] = 'form-control'


class CompanyUserCreateForm(CompanyUserAccessForm, UserCreationForm):
    class Meta(CompanyUserAccessForm.Meta):
        fields = ('username', *CompanyUserAccessForm.Meta.fields, 'password1', 'password2')
