"""Company access profiles, materialized for existing permission and task queries."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models.signals import post_save, m2m_changed, pre_save
from django.dispatch import receiver
from django.shortcuts import get_object_or_404, redirect, render
from core.permissions import require_roles, ROLES
from .models import AccessGroup, User


def apply_group(user):
    group = user.access_group
    if group is None:
        return
    if user.company_id != group.company_id or user.is_superuser or user.is_staff:
        raise ValidationError('Группа доступна только обычным пользователям своей компании.')
    User.objects.filter(pk=user.pk).update(role_id=group.role_id)
    user.role_id = group.role_id
    user.assigned_objects.set(group.assigned_objects.all())


@receiver(pre_save, sender=User)
def validate_user_group(sender, instance, **kwargs):
    if instance.access_group_id:
        group = instance.access_group
        if group.company_id != instance.company_id or instance.is_superuser or instance.is_staff:
            raise ValidationError('Нельзя назначить эту группу пользователю.')
        instance.role_id = group.role_id


@receiver(post_save, sender=User)
def sync_user_group(sender, instance, **kwargs):
    if instance.access_group_id:
        apply_group(instance)


@receiver(post_save, sender=AccessGroup)
def sync_group_role(sender, instance, **kwargs):
    for user in instance.members.all():
        apply_group(user)


@receiver(m2m_changed, sender=AccessGroup.assigned_objects.through)
def sync_group_objects(sender, instance, action, reverse, pk_set, **kwargs):
    if reverse:
        if action.startswith('pre_'):
            raise ValidationError('Изменяйте объекты через группу доступа.')
        return
    if action == 'pre_add':
        from apps.projects.models import ConstructionObject
        if ConstructionObject.objects.filter(pk__in=pk_set).exclude(company_id=instance.company_id).exists():
            raise ValidationError('Нельзя назначить объект другой компании.')
    if action in {'post_add', 'post_remove', 'post_clear'}:
        for user in instance.members.all():
            apply_group(user)


class GroupForm(forms.ModelForm):
    members = forms.ModelMultipleChoiceField(queryset=User.objects.none(), required=False, label='Участники группы')

    class Meta:
        model = AccessGroup
        fields = ['name', 'role', 'assigned_objects', 'ad_group_dn']

    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = company
        self.fields['role'].queryset = self.fields['role'].queryset.filter(code__in=ROLES - {'ADMIN'})
        self.fields['assigned_objects'].queryset = self.fields['assigned_objects'].queryset.filter(company=company)
        self.fields['members'].queryset = User.objects.filter(company=company, is_superuser=False, is_staff=False).exclude(role__code='ADMIN')
        if self.instance.pk:
            self.initial['members'] = self.instance.members.values_list('pk', flat=True)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if AccessGroup.objects.filter(company=self.instance.company, name=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Группа с таким названием уже существует.')
        return name

    def clean_ad_group_dn(self):
        dn = self.cleaned_data['ad_group_dn'].strip()
        if dn and (not dn.lower().startswith('cn=') or ',dc=' not in dn.lower() or '\x00' in dn):
            raise forms.ValidationError('Укажите полный DN: CN=Группа,OU=...,DC=...,DC=...')
        if dn and AccessGroup.objects.filter(ad_group_dn__iexact=dn).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Эта группа AD уже связана с группой доступа.')
        return dn


@login_required
def groups(request, pk=None):
    require_roles(request.user, {'ADMIN'})
    group = get_object_or_404(AccessGroup, pk=pk, company=request.user.company) if pk else AccessGroup(company=request.user.company)
    form = GroupForm(request.POST or None, company=request.user.company, instance=group)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            group = form.save()
            selected = form.cleaned_data['members']
            # Removing a group also removes the rights inherited from it.
            for user in group.members.exclude(pk__in=selected.values('pk')):
                user.access_group = None
                user.access_group_from_ad = False
                user.role = None
                user.save(update_fields=['access_group', 'access_group_from_ad', 'role'])
                user.assigned_objects.clear()
            for user in selected:
                if user.access_group_id != group.pk:
                    user.access_group_from_ad = False
                user.access_group = group
                user.save(update_fields=['access_group', 'access_group_from_ad', 'role'])
        messages.success(request, 'Группа и права участников обновлены.')
        return redirect('accounts:access_groups')
    return render(request, 'accounts/access_groups.html', {'form': form, 'editing': bool(pk), 'groups': AccessGroup.objects.filter(company=request.user.company).select_related('role').prefetch_related('assigned_objects', 'members')})


def map_directory_group(user, identity):
    """Called only after a verified AD bind; multiple matches fail closed."""
    dns = {dn.casefold() for dn in identity.get('group_dns', [])}
    # LDAP DNs are case insensitive, SQL comparisons may not be.
    matches = [group for group in AccessGroup.objects.exclude(ad_group_dn='') if group.ad_group_dn.casefold() in dns]
    if len(matches) > 1:
        raise PermissionDenied('Несколько групп доступа AD. Обратитесь к администратору.')
    if not matches:
        if user.access_group_from_ad:
            user.access_group = None
            user.access_group_from_ad = False
            user.role = None
            user.save(update_fields=['access_group', 'access_group_from_ad', 'role'])
            user.assigned_objects.clear()
        return
    group = matches[0]
    if user.is_superuser or user.is_staff or (user.role_id and user.role.code == 'ADMIN'):
        raise PermissionDenied
    if user.company_id and user.company_id != group.company_id:
        raise PermissionDenied
    user.company = group.company
    user.access_group = group
    user.access_group_from_ad = True
    user.role_id = group.role_id
    user.save(update_fields=['company', 'access_group', 'access_group_from_ad', 'role'])


@receiver(pre_save, sender=AccessGroup)
def protect_group(sender, instance, **kwargs):
    if instance.role.code == 'ADMIN':
        raise ValidationError('Права администратора назначаются индивидуально.')
    if instance.pk:
        old = AccessGroup.objects.get(pk=instance.pk)
        if old.company_id != instance.company_id:
            raise ValidationError('Компания группы не может быть изменена.')
