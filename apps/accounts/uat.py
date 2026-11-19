"""Read-only discovery of the 1C UAT source using the existing ZUP identity."""
import re

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import Paginator
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from core.permissions import require_roles
from .models import OneCConnection
from .one_c_catalog import catalogue, TYPES


KEYWORDS = ('путев', 'ремонт', 'транспорт', 'автомоб', 'техник', 'водител', 'маршрут', 'топлив', 'пробег', 'неисправ')


def database_name():
    value = settings.UAT_DATABASE
    if not re.fullmatch(r'[\w\-]+', value):
        raise ImproperlyConfigured('UAT_DATABASE содержит недопустимые символы.')
    return value


@login_required
@require_http_methods(['GET', 'POST'])
def discovery(request):
    require_roles(request.user, {'ADMIN'})
    connection, _ = OneCConnection.objects.get_or_create(company=request.user.company)
    database = database_name()
    if request.method == 'POST':
        if request.POST.get('action') != 'diagnose':
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Неизвестное действие.')
        from .one_c import enqueue
        run = enqueue(connection, request.user, database=database, uat_discovery=True)
        messages.success(request, 'Диагностика УАТ поставлена в очередь.' if run else
            'Уже выполняется другое задание 1С. Дождитесь его завершения.')
        return redirect('accounts:uat')

    runs = connection.runs.filter(database=database, report__uat_discovery=True).order_by('-created_at', '-pk')
    latest = runs.filter(status='DONE').first()
    report = latest.report if latest else {}
    labels = report.get('metadata_labels', {})
    all_rows, type_counts, total = catalogue(report, labels)
    recommended = [row for row in all_rows if any(word in (row['name'] + ' ' + row['key']).casefold() for word in KEYWORDS)]
    query = request.GET.get('q', '').strip()[:200]
    kind = request.GET.get('type', '')
    if kind not in dict(TYPES):kind = ''
    rows, _, _ = catalogue(report, labels, query, kind)
    page = Paginator(rows, 75).get_page(request.GET.get('page'))
    groups = []
    for row in page:
        row['type_label'] = dict(TYPES)[row['type']]
        if not groups or groups[-1]['type'] != row['type']:
            groups.append({'type': row['type'], 'label': row['type_label'], 'rows': []})
        groups[-1]['rows'].append(row)
    params = request.GET.copy();params.pop('page', None)
    return render(request, 'accounts/uat.html', {
        'connection': connection, 'database': database, 'latest': latest, 'pending': runs.filter(status__in=['QUEUED', 'RUNNING']).exists(),
        'recommended': recommended[:100], 'catalogue_groups': groups, 'catalogue_page': page,
        'catalogue_total': total, 'catalogue_query': query, 'catalogue_type': kind,
        'type_counts': type_counts, 'catalogue_params': params.urlencode(),
    })
