from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


class CompanyAccessMiddleware:
    """Require authentication and company membership before application views run."""

    public_views = {'accounts:login', 'accounts:register'}
    account_views = {'accounts:logout', 'accounts:settings', 'accounts:password_change', 'accounts:access_pending', 'accounts:impersonation_stop'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .fact_history import history_actor
        with history_actor(request.user):
            return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        # Django admin is a global administration interface, reserved for superusers.
        if 'admin' in match.namespaces:
            if request.user.is_authenticated and not request.user.is_superuser:
                raise PermissionDenied('Глобальная админ-панель доступна только суперпользователю.')
            return None
        if match.view_name in self.public_views:
            return None
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if request.user.auth_source=='LDAP' and (not request.user.company_id or not request.user.role_id) and match.view_name not in self.account_views:
            from django.shortcuts import redirect
            return redirect('accounts:access_pending')
        if match.view_name not in self.account_views and not request.user.company_id:
            raise PermissionDenied('Для доступа к данным администратор должен назначить вам компанию.')
        from core.permissions import check_route, role_code, DISPATCHER_ROLES, PERSONNEL_CATALOG_MANAGER, EQUIPMENT_CATALOG_MANAGER
        catalogue_home = {PERSONNEL_CATALOG_MANAGER: 'resources:employee_list',
                          EQUIPMENT_CATALOG_MANAGER: 'resources:equipment_type_list'}
        if match.namespace == 'dashboard' and match.url_name == 'index' and role_code(request.user) in catalogue_home:
            from django.shortcuts import redirect
            return redirect(catalogue_home[role_code(request.user)])
        if match.namespace=='dashboard' and role_code(request.user)=='COMMANDANT':
            from django.shortcuts import redirect
            return redirect('personnel:lodging')
        if match.namespace=='production' and match.url_name=='fact_day_workspace' and role_code(request.user)=='FUEL_WAREHOUSE' and request.method in {'GET','HEAD'}:
            from django.shortcuts import redirect
            return redirect('production:camp_fuel')
        if match.namespace == 'dashboard' and role_code(request.user) == 'FUEL_WAREHOUSE':
            from django.shortcuts import redirect
            return redirect('production:camp_fuel')
        if match.namespace == 'dashboard' and role_code(request.user) in DISPATCHER_ROLES:
            from django.shortcuts import redirect
            return redirect('planning:task_list')
        if match.namespace == 'dashboard' and role_code(request.user) == 'FOREMAN':
            from django.shortcuts import redirect
            return redirect('production:fact_day_workspace')
        if request.path != "/":
            check_route(request.user, match, request.method)
        from core.planning_scope import check_planning_scope
        check_planning_scope(request, match)
        return None
