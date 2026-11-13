from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.utils import timezone
from django import forms

from apps.resources.models import Employee
from core.permissions import ROLES, require_roles


@login_required
@require_GET
def search(request):
    """Small, company-scoped employee pages for remote form selects."""
    require_roles(request.user, ROLES)
    query = Employee.objects.filter(company=request.user.company).select_related('position')
    if request.GET.get('scope') == 'movement':
        kind = request.GET.get('kind', 'ARRIVAL')
        if kind == 'ARRIVAL':
            query = query.filter(is_active=True)
        else:
            from .housing import present_stays
            try:
                day = forms.DateField().clean(request.GET.get('date')) or timezone.localdate()
            except forms.ValidationError:
                day = timezone.localdate()
            present = present_stays(request.user.company, day)
            camp = request.GET.get('camp')
            if camp and camp.isdigit():
                present = present.filter(camp_id=camp)
            query = query.filter(pk__in=present.values('person__employee_id'))
    elif request.GET.get('active', '1') != '0':
        query = query.filter(is_active=True)
    query = query.exclude(personnel_card__dispatcher_removed_at__isnull=False)
    for term in request.GET.get('q', '').strip().split()[:6]:
        query = query.filter(
            Q(last_name__icontains=term) | Q(first_name__icontains=term) |
            Q(middle_name__icontains=term) | Q(employee_number__icontains=term) |
            Q(position__name__icontains=term)
        )
    query = query.distinct().order_by('last_name', 'first_name', 'middle_name', 'employee_number', 'pk')
    page = Paginator(query, 50).get_page(request.GET.get('page'))
    items = []
    for employee in page.object_list:
        label = employee.display_name
        if employee.employee_number:
            label += f' · № {employee.employee_number}'
        if employee.position_id:
            label += f' · {employee.position}'
        if not employee.is_active:
            label += ' · архивный'
        items.append({'id': employee.pk, 'text': label})
    return JsonResponse({
        'items': items,
        'page': page.number,
        'pages': page.paginator.num_pages,
        'total': page.paginator.count,
        'has_next': page.has_next(),
    })
