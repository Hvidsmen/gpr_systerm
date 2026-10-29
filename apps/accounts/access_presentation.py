"""Shared access form configuration for AD and local accounts."""
from .models import AccessGroup


def configure_access(form, company, objects_name):
    groups = list(AccessGroup.objects.filter(company=company).select_related('role').prefetch_related('assigned_objects'))
    form.fields['access_group'].queryset = AccessGroup.objects.filter(company=company)
    form.fields['access_group'].empty_label = 'Без группы — индивидуальные права'
    form.fields['access_group'].help_text = 'Выберите группу, чтобы автоматически назначить её роль и объекты.'
    form.fields['role'].label = 'Роль'
    form.fields['role'].empty_label = 'Выберите роль'
    form.fields['role'].help_text = 'Определяет доступные разделы и действия.'
    form.fields[objects_name].label = 'Объекты и субобъекты'
    form.fields[objects_name].help_text = 'Для ограниченных ролей выберите объекты. Доступ к ВЖГ определяется их связями с объектами.'
    form.group_options = {
        'groups': [{'id': group.pk, 'role': group.role_id, 'objects': [obj.pk for obj in group.assigned_objects.all()]} for group in groups],
        'roles': {str(role.pk): role.code for role in form.fields['role'].queryset},
    }
    selected = form.data.get(form.add_prefix('access_group')) if form.is_bound else form.initial.get('access_group')
    group = next((group for group in groups if str(group.pk) == str(selected)), None)
    if group:
        for name, value in [('role', group.role_id), (objects_name, [obj.pk for obj in group.assigned_objects.all()])]:
            form.fields[name].disabled = True
            form.initial[name] = value
