from django.contrib import admin
from django import forms
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import Company, Role, User


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ['name', 'inn', 'is_active', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name', 'inn']


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'created_at']
    search_fields = ['name', 'code']


class GlobalUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = '__all__'

    def clean(self):
        data = super().clean()
        objects = data.get('assigned_objects')
        company = data.get('company')
        if objects is not None and objects.exclude(company=company).exists():
            self.add_error('assigned_objects', 'Назначенные объекты должны принадлежать компании пользователя.')
        return data


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    form = GlobalUserForm
    list_display = ['username', 'email', 'full_name', 'company', 'role', 'is_active']
    list_filter = ['is_active', 'company', 'role']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    fieldsets = BaseUserAdmin.fieldsets + (
        (_('Дополнительная информация'), {
            'fields': ('company', 'role', 'position', 'phone', 'assigned_objects')
        }),
    )