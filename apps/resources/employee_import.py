from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST
from core.permissions import require_roles
from apps.accounts.models import OneCConnection
from apps.accounts.one_c import enqueue
from apps.accounts.one_c_employees import source_confirmed


@login_required
@require_POST
def employee_zup_import(request):
    require_roles(request.user, {'ADMIN'})
    connection = OneCConnection.objects.filter(company_id=request.user.company_id).first()
    if connection is None or not source_confirmed(connection):
        messages.error(request, 'Сначала выполните диагностику подключения ЗУП и прочитайте названия таблиц сотрудников.')
        return redirect('accounts:one_c')
    run = enqueue(connection, request.user, employees=True)
    messages.success(request, 'Загрузка списка сотрудников поставлена в очередь. Результат появится в истории заданий 1С.' if run else 'Уже выполняется задание 1С. Дождитесь его завершения и повторите загрузку.')
    return redirect('accounts:one_c_jobs')
