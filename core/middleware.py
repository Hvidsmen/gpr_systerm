from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


class CompanyAccessMiddleware:
    """Require authentication and company membership before application views run."""

    public_views = {'accounts:login', 'accounts:register'}
    account_views = {'accounts:logout', 'accounts:settings'}

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
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
        if match.view_name not in self.account_views and not request.user.company_id:
            raise PermissionDenied('Для доступа к данным администратор должен назначить вам компанию.')
        return None
