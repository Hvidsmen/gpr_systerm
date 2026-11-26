"""Section-scoped editing during collaborative GPR/SMG formation."""
from django.core.exceptions import PermissionDenied
from core.permissions import role_code
from .models import PlanLaborGroupPermission


FORMATION_RESOURCE_ROLES={'MANAGER','SITE_CHIEF','TECH_HEAD','SBO_HEAD'}


def allowed_group_ids(user):
    if role_code(user) not in {'TECH_HEAD','SBO_HEAD'}:return set()
    return set(PlanLaborGroupPermission.objects.filter(
        company=user.company,role_code=role_code(user),group__company=user.company
    ).values_list('group_id',flat=True))


def can_edit_resource(user,owner,kind,brigade=None):
    role=role_code(user)
    if role in {'ADMIN','PLANNER'}:return True
    if role in {'MANAGER','SITE_CHIEF'}:
        from .preparation import resource_editor_allowed
        return resource_editor_allowed(user,owner)
    if role=='TECH_HEAD' and kind in {'equipment','fuel'}:return True
    if role in {'TECH_HEAD','SBO_HEAD'} and kind=='labor':
        group_id=getattr(brigade,'group_id',None)
        return bool(group_id and group_id in allowed_group_ids(user))
    return False


def can_edit_any_resource(user,owner):
    role=role_code(user)
    if role in {'ADMIN','PLANNER'}:return True
    if role in {'MANAGER','SITE_CHIEF'}:
        from .preparation import resource_editor_allowed
        return resource_editor_allowed(user,owner)
    if role=='TECH_HEAD':return True
    return role=='SBO_HEAD' and bool(allowed_group_ids(user))


def entry_brigade(entry):
    if entry.kind!='labor':return None
    from apps.resources.models import Brigade
    row=next(iter(entry.rows or entry.original),{})
    return Brigade.objects.filter(company_id=entry.schedule.company_id,pk=row.get('brigade_id')).select_related('group').first()


def require_entry(user,entry):
    if not can_edit_resource(user,entry.schedule,entry.kind,entry_brigade(entry)):
        raise PermissionDenied('Эта строка не относится к вашему разделу формирования плана.')


def require_month_row(user,owner,row):
    if not can_edit_resource(user,owner,row.kind,getattr(row,'brigade',None)):
        raise PermissionDenied('Эта строка не относится к вашему разделу формирования плана.')


def configure_add_form(form,user,owner):
    role=role_code(user)
    if role in {'ADMIN','PLANNER'}:return form
    kinds=[]
    if role in {'MANAGER','SITE_CHIEF'} and can_edit_any_resource(user,owner):kinds=['labor','equipment','fuel']
    elif role=='TECH_HEAD':kinds=['labor','equipment','fuel']
    elif role=='SBO_HEAD':kinds=['labor']
    form.fields['kind'].choices=[choice for choice in form.fields['kind'].choices if choice[0] in kinds]
    if 'brigade' in form.fields and role in {'TECH_HEAD','SBO_HEAD'}:
        form.fields['brigade'].queryset=form.fields['brigade'].queryset.filter(group_id__in=allowed_group_ids(user))
    return form
