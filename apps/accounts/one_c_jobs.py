"""Administrator's company-scoped queue and history of 1C jobs."""
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET
from core.permissions import require_roles
from .models import OneCDiagnosticRun

KINDS = [('diagnostic', 'Диагностика структуры'), ('metadata', 'Чтение названий'), ('positions', 'Загрузка должностей')]


@login_required
@require_GET
def jobs(request):
    require_roles(request.user, {'ADMIN'})
    query = OneCDiagnosticRun.objects.filter(connection__company=request.user.company).select_related('created_by').order_by('-created_at', '-pk')
    pending = query.filter(status__in=['QUEUED', 'RUNNING']).exists()
    status = request.GET.get('status', '')
    statuses = OneCDiagnosticRun._meta.get_field('status').choices
    if status in dict(statuses):
        query = query.filter(status=status)
    else:
        status = ''
    kind = request.GET.get('kind', '')
    if kind == 'positions':
        query = query.filter(report__positions_import=True)
    elif kind == 'metadata':
        query = query.filter(report__metadata_only=True).exclude(report__has_key='positions_import')
    elif kind == 'diagnostic':
        query = query.exclude(report__has_key='positions_import').exclude(report__has_key='metadata_only')
    else:
        kind = ''
    page = Paginator(query, 50).get_page(request.GET.get('page'))
    for run in page:
        run.kind_label = 'Загрузка должностей' if run.report.get('positions_import') else 'Чтение названий' if run.report.get('metadata_only') else 'Диагностика структуры'
    params = request.GET.copy()
    params.pop('page', None)
    return render(request, 'accounts/one_c_jobs.html', {'page': page, 'pending': pending, 'statuses': statuses,
                  'kinds': KINDS, 'selected_status': status, 'selected_kind': kind, 'params': params.urlencode()})
