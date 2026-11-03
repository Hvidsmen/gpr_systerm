from django.contrib.auth.decorators import login_required


@login_required
def dashboard_view(request):
    from core.permissions import role_code
    if role_code(request.user) == 'RECRUITER':
        from django.shortcuts import redirect
        return redirect('personnel:recruitment_list')
    from apps.planning.report_views import OverviewReport
    return OverviewReport.as_view()(request)
