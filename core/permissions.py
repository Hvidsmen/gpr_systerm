"""Central role policy and construction-object scope for planning and fact entry."""
from django.core.exceptions import PermissionDenied

from .plan_approval import DEPARTMENT_ROLES, ACTION_ROLES
GLOBAL_APPROVAL_ROLES = DEPARTMENT_ROLES | {'CEO'}
ALL_FACT_DISPATCHER = 'ALL_FACT_DISPATCHER'
DISPATCHER_ROLES = {'DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER',ALL_FACT_DISPATCHER}
ROLES = DISPATCHER_ROLES | {'ADMIN', 'PLANNER', 'MANAGER', 'FOREMAN', 'DISPATCHER', 'OBSERVER', 'ESTIMATOR'} | GLOBAL_APPROVAL_ROLES
READ_ROLES = {'ADMIN', 'PLANNER', 'MANAGER', 'OBSERVER', 'ESTIMATOR'} | GLOBAL_APPROVAL_ROLES
PLAN_ROLES = {'ADMIN', 'PLANNER'}
FACT_ROLES = {'ADMIN', 'FOREMAN'} | DISPATCHER_ROLES
APPROVAL_ROLES = {'ADMIN', 'MANAGER'}


def role_code(user):
    if user.is_superuser:
        return 'ADMIN'
    return user.role.code if user.role_id else None


def require_roles(user, roles):
    if not user.is_authenticated or not user.company_id or role_code(user) not in roles:
        raise PermissionDenied('Роль пользователя не разрешает это действие.')


def approval_role(user):
    if role_code(user) == 'ADMIN':
        return 'ADMIN'
    code = user.role.code if user.role_id else None
    return code if code in GLOBAL_APPROVAL_ROLES else role_code(user)


def require_global_action(user, action):
    roles = {
        'submit': {'MANAGER'},
        **ACTION_ROLES,
        'approve': {'CEO'},
        'complete': {'CEO'},
        'reject': GLOBAL_APPROVAL_ROLES,
    }.get(action)
    if roles is None:
        require_roles(user, PLAN_ROLES)
    elif not user.is_authenticated or not user.company_id or approval_role(user) not in roles | {'ADMIN'}:
        raise PermissionDenied('Действие доступно ответственному руководителю, генеральному директору или администратору.')


def scope_queryset(queryset, user, include_archived=False):
    """Always company scope; planners, masters and dispatchers see only assigned objects."""
    name = queryset.model._meta.label
    queryset = queryset.filter(**{('schedule__company' if name == 'planning.DailyScheduleEntry' else 'company'): user.company})
    if not include_archived:
        archive_paths = {
            'planning.PlanningWorkspace':'is_archived',
            'planning.GlobalPlanVersion':'workspace__is_archived',
            'planning.CarryoverReviewTask':'version__workspace__is_archived',
        }
        if name in archive_paths:
            queryset = queryset.exclude(**{archive_paths[name]:True})
        if name in {'planning.EstimateReviewTask','planning.WorkTask'}:
            from django.db.models import Q
            queryset = queryset.exclude(Q(version__workspace__is_archived=True) & Q(schedule__isnull=True))
    if role_code(user) not in ({'FOREMAN','PLANNER'} | DISPATCHER_ROLES):
        return queryset
    paths = {
        'projects.ConstructionObject': 'pk',
        'projects.Section': 'construction_object_id',
        'projects.Project': 'construction_objects__pk',
        'works.ProjectWork': 'section__construction_object_id',
        'works.ProjectWorkItem': 'project_work__section__construction_object_id',
        'production.DailyFact': 'project_work__section__construction_object_id',
        'planning.MonthlyPlan': 'project_work__section__construction_object_id',
        'planning.PlanVersion': 'monthly_plan__project_work__section__construction_object_id',
        'planning.DailyPlan': 'plan_version__monthly_plan__project_work__section__construction_object_id',
        'planning.DailySchedule': 'construction_object_id',
        'planning.DailyScheduleEntry': 'schedule__construction_object_id',
        'planning.CarryoverReviewTask': 'version__construction_object_id',
        'planning.EstimateReviewTask': 'version__construction_object_id',
        'planning.ProjectPlanVersion': 'members__construction_object_id',
    }
    if name == 'planning.ProjectPlanVersion' and role_code(user) == 'PLANNER':
        from apps.projects.models import ConstructionObject
        permitted = user.assigned_objects.filter(company=user.company).values('pk')
        other = ConstructionObject.objects.filter(company=user.company).exclude(pk__in=permitted)
        return queryset.filter(project__construction_objects__pk__in=permitted).exclude(members__construction_object__in=other).distinct()
    if name == 'planning.WorkTask' and role_code(user) == 'PLANNER':
        from django.db.models import Q
        return queryset.filter(Q(construction_object__in=user.assigned_objects.filter(company=user.company)) | Q(construction_object__isnull=True))
    if name == 'planning.EstimateReviewTask':
        from django.db.models import Q
        ids = user.assigned_objects.filter(company=user.company).values('pk')
        return queryset.filter(Q(version__construction_object_id__in=ids) | Q(schedule__construction_object_id__in=ids))
    path = paths.get(name)
    if path is None and any(field.name == 'construction_object' for field in queryset.model._meta.fields):
        path = 'construction_object_id'
    if path:
        return queryset.filter(**{path + '__in': user.assigned_objects.filter(company=user.company).values('pk')}).distinct()
    return queryset


def check_route(user, match, method):
    namespace, name = match.namespace, match.url_name
    role = role_code(user)
    if namespace == 'production' and name in {'equipment_assignment_list', 'equipment_history_detail'}:
        require_roles(user, READ_ROLES | {'MECHANIZATION_DISPATCHER', 'ALL_FACT_DISPATCHER'} if method in {'GET', 'HEAD'} else {'ADMIN', 'MECHANIZATION_DISPATCHER', 'ALL_FACT_DISPATCHER'});return
    if namespace == 'resources' and name in {'employee_zup_import','employee_position_report','brigade_zup_employees'}:
        require_roles(user, {'ADMIN'});return
    if namespace == 'resources' and name in {'employee_create', 'employee_update'}:
        require_roles(user, {'ADMIN', 'HR_HEAD', 'PLANNER'});return
    if namespace == 'accounts' and name in {'password_change','access_pending','impersonation_stop'}:
        return
    if namespace == 'planning' and name=='workspace_edit':
        require_roles(user, READ_ROLES if method in {'GET','HEAD'} else PLAN_ROLES|{'MANAGER'});return
    if namespace == 'planning' and name=='smg_copy_gpr':
        require_roles(user,PLAN_ROLES|{'MANAGER'});return
    if namespace == 'planning' and name.startswith('object_plan_'):
        require_roles(user, READ_ROLES if name in {'object_plan_list','object_plan_detail'} and method in {'GET','HEAD'} else PLAN_ROLES)
        return
    if namespace == 'planning' and name in {'pivot_report_list','pivot_fact_list','pivot_gpr_detail','pivot_smg_detail'}:
        require_roles(user, READ_ROLES)
        return
    if namespace == 'personnel':
        require_roles(user, {'ADMIN', 'HR_HEAD', 'PERSONNEL_DISPATCHER'})
        return
    if namespace == 'planning' and name in {'work_resources_gpr','work_resources_smg','work_resource_edit_gpr','work_resource_edit_smg','work_resources_graph_gpr','work_resources_graph_smg','work_resources_links_gpr','work_resources_links_smg','work_resources_save_smg'}:
        require_roles(user, READ_ROLES if method in {'GET','HEAD'} else {'ADMIN','PLANNER','MANAGER'})
        return
    if (namespace=='planning' and name in {'design_preview','decomposition_report','decomposition_gpr','decomposition_smg'}) or (namespace=='resources' and name=='equipment_crew'):
        require_roles(user,READ_ROLES if method in {'GET','HEAD'} else PLAN_ROLES)
        return
    if role == 'OBSERVER':
        require_roles(user, READ_ROLES)
        if namespace == 'accounts' and name == 'logout':
            return
        if method not in {'GET', 'HEAD', 'OPTIONS'}:
            raise PermissionDenied('Наблюдателю доступен только просмотр данных.')
        if namespace == 'accounts':
            if name == 'settings' or name == 'user_detail' and match.kwargs.get('pk') == user.pk:
                return
        elif namespace in {'dashboard', 'planning', 'production', 'rotation', 'projects', 'works', 'resources', 'estimates'}:
            if name.endswith(('_list', '_detail', '_export', '_versions')) or name in {'daily_schedule_edit', 'index', 'revenue', 'report_matrix', 'plan_matrix', 'fact_day_workspace', 'fact_daily', 'fact_input'}:
                return
        raise PermissionDenied('Наблюдателю доступен только просмотр данных.')
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
        if name in {'fact_day_workspace','work_claims'}:
            require_roles(user, ROLES if method in {'GET','HEAD'} else FACT_ROLES)
            return
        if role in DISPATCHER_ROLES and method not in {'GET','HEAD'}:
            # Dispatchers enter their own direction through the validated daily workspace.
            raise PermissionDenied('Вносите факт своего направления через «Факт за день».')
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
    if namespace == 'estimates':
        if role == 'PLANNER':
            raise PermissionDenied('Раздел смет недоступен планировщику.')
        require_roles(user, READ_ROLES if name in {'estimate_list', 'estimate_report_list', 'estimate_detail', 'catalog_list', 'estimate_export'} and method in {'GET', 'HEAD'} else PLAN_ROLES)
        return
    if namespace == 'rotation':
        require_roles(user, READ_ROLES if name in {'plan_list', 'plan_detail', 'plan_export'} and method in {'GET', 'HEAD'} else PLAN_ROLES)
        return
    if namespace == 'planning':
        if name in {'workspace_delete','global_delete','task_force_close'}:
            require_roles(user,{'ADMIN'});return
        if name.startswith('task_'):
            require_roles(user,ROLES)
            return
        if name=='workspace_add_resource' or name=='workspace_bulk_add' and match.kwargs.get('kind')!='works':
            require_roles(user,{'ADMIN','PLANNER','MANAGER'});return
        if name.startswith('preparation_') or name.startswith('work_resources_'):
            require_roles(user,READ_ROLES if method in {'GET','HEAD'} else {'ADMIN','PLANNER','MANAGER'})
            return
        if name.startswith('estimate_review_'):
            require_roles(user, {'ADMIN','PLANNER','ESTIMATOR'})
            return
        if name == 'daily_schedule_edit':
            require_roles(user, READ_ROLES if method in {'GET','HEAD'} else PLAN_ROLES | {'MANAGER'})
            return
        if name=='version_submit':
            require_roles(user,{'ADMIN','MANAGER'});return
        action = match.kwargs.get('action') if name == 'global_action' else None
        if name == 'global_action':
            require_global_action(user, action)
        elif name == 'project_plan_detail' and method not in {'GET', 'HEAD'}:
            require_roles(user, PLAN_ROLES)
        elif name in {'version_approve', 'version_reject', 'version_complete'}:
            require_roles(user, APPROVAL_ROLES)
        elif name.endswith(('_list', '_detail', '_export', '_versions')) or name in {'plan_matrix','report_matrix'}:
            require_roles(user, READ_ROLES)
        else:
            require_roles(user, PLAN_ROLES)
        return
    if namespace in {'projects', 'works', 'resources'}:
        if namespace == 'projects' and name in {'section_purge', 'section_delete'}:
            require_roles(user, {'ADMIN'}); return
        if namespace=='resources' and (name in {'catalogue_purge','brigade_delete','equipment_type_delete'} or name=='bulk_delete' and match.kwargs.get('kind') in {'brigades','equipment'}):
            require_roles(user,{'ADMIN'});return
        if namespace=='works' and (name in {'work_delete','work_item_delete','work_purge','complex_purge','legacy_structure_bulk'} or name=='bulk_delete' and match.kwargs.get('kind')=='works'):
            require_roles(user,{'ADMIN'});return
        require_roles(user, READ_ROLES if name.endswith(('_list', '_detail')) or namespace == 'resources' and name.endswith('_export') else PLAN_ROLES)
        return
    raise PermissionDenied('Доступ к этому разделу не разрешён.')


def require_planning_object(user, object_id):
    if role_code(user) == 'PLANNER' and not user.assigned_objects.filter(company=user.company, pk=object_id).exists():
        raise PermissionDenied('Объект не назначен планировщику. Обратитесь к администратору.')
