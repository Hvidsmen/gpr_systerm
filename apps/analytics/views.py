from django.contrib.auth.decorators import login_required


@login_required
def dashboard_view(request):
    from apps.planning.report_views import OverviewReport
    return OverviewReport.as_view()(request)
