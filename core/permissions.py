"""Central role policy and construction-object scope for fact entry."""
from django.core.exceptions import PermissionDenied

ROLES = {'ADMIN', 'PLANNER', 'MANAGER', 'FOREMAN'}
READ_ROLES = {'ADMIN', 'PLANNER', 'MANAGER'}
PLAN_ROLES = {'ADMIN', 'PLANNER'}
FACT_ROLES = {'ADMIN', 'FOREMAN'}
APPROVAL_ROLES = {'ADMIN', 'MANAGER'}


def role_code(user):
    if user.is_superuser:
        return 'ADMIN'
    return user.role.code if user.role_id else None


def require_roles(user, roles):
    if not user.is_authenticated or not user.company_id or role_code(user) not in roles:
        raise PermissionDenied('Роль пользователя не разрешает это действие.')


def scope_queryset(queryset, user):
    """Always company scope; masters additionally see assigned construction objects."""
    queryset = queryset.filter(company=user.company)
    if role_code(user) != 'FOREMAN':
        return queryset
    name = queryset.model._meta.label
    paths = {
        'projects.ConstructionObject': 'pk',
        'projects.Section': 'construction_object_id',
        'projects.Project': 'construction_objects__pk',
        'works.ProjectWork': 'section__construction_object_id',
        'works.ProjectWorkItem': 'project_work__section__construction_object_id',
        'production.DailyFact': 'project_work__section__construction_object_id',
    }
    path = paths.get(name)
    if path is None and any(field.name == 'construction_object' for field in queryset.model._meta.fields):
        path = 'construction_object_id'
    if path:
        return queryset.filter(**{path + '__in': user.assigned_objects.filter(company=user.company).values('pk')}).distinct()
    return queryset


def check_route(user, match, method):
    namespace, name = match.namespace, match.url_name
    role = role_code(user)
    if namespace == 'accounts':
        if name in {'settings', 'logout'}:
            return
        if name == 'user_detail' and match.kwargs.get('pk') == user.pk:
            return
        require_roles(user, {'ADMIN'})
        return
    require_roles(user, ROLES)
    if namespace == 'dashboard':
        require_roles(user, READ_ROLES)
        return
    if namespace == 'production':
        if name in {'legacy_resources', 'legacy_resolve'}:
            require_roles(user, {'ADMIN'})
            return
        is_plan = '_plan_' in name
        if is_plan:
            roles = READ_ROLES if name.endswith('_list') else PLAN_ROLES
        elif name in {'fact_list', 'labor_list', 'equipment_list', 'fuel_list'} or name.endswith('_fact_list'):
            roles = ROLES
        elif name in {'fact_day_workspace', 'fact_daily', 'fact_input'} and method in {'GET', 'HEAD'}:
            roles = ROLES
        else:
            roles = FACT_ROLES
        require_roles(user, roles)
        return
    if namespace == 'planning':
        action = match.kwargs.get('action') if name == 'global_action' else None
        if name in {'version_approve', 'version_reject', 'version_complete'} or action in {'approve', 'reject', 'complete'}:
            require_roles(user, APPROVAL_ROLES)
        elif name.endswith(('_list', '_detail', '_export', '_versions')) or name == 'plan_matrix':
            require_roles(user, READ_ROLES)
        else:
            require_roles(user, PLAN_ROLES)
        return
    if namespace in {'projects', 'works', 'resources'}:
        require_roles(user, READ_ROLES if name.endswith(('_list', '_detail')) or namespace == 'resources' and name.endswith('_export') else PLAN_ROLES)
        return
    raise PermissionDenied('Доступ к этому разделу не разрешён.')
