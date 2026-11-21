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
    scope = request.GET.get('scope')
    lodging_kind = ''
    lodging_camp_id = None
    lodging_residents = None
    if scope == 'movement':
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
    elif scope == 'lodging':
        from .lodging import overlapping_from, present
        try:
            day = forms.DateField().clean(request.GET.get('date')) or timezone.localdate()
        except forms.ValidationError:
            day = timezone.localdate()
        lodging_kind = request.GET.get('kind', 'CHECKIN')
        lodging_residents = overlapping_from(request.user.company, day) if lodging_kind == 'CHECKIN' else present(request.user.company, day)
        camp = request.GET.get('camp')
        if camp and camp.isdigit():
            lodging_camp_id = int(camp)
        if lodging_kind == 'CHECKIN':
            query = query.filter(is_active=True)
        else:
            # Active employees remain searchable so the form can explain that
            # they have not been lodged yet.  Archived employees are retained
            # only while they have an actual open accommodation record.
            query = query.filter(Q(is_active=True)|Q(pk__in=lodging_residents.values('employee_id')))
    elif request.GET.get('active', '1') != '0':
        query = query.filter(is_active=True)
    if scope == 'lodging':
        query = query.filter(
            Q(personnel_card__dispatcher_removed_at__isnull=True) |
            Q(pk__in=lodging_residents.values('employee_id'))
        )
    elif scope != 'assignment':
        query = query.exclude(personnel_card__dispatcher_removed_at__isnull=False)
    for term in request.GET.get('q', '').strip().split()[:6]:
        query = query.filter(
            Q(last_name__icontains=term) | Q(first_name__icontains=term) |
            Q(middle_name__icontains=term) | Q(employee_number__icontains=term) |
            Q(position__name__icontains=term)
        )
    query = query.distinct().order_by('last_name', 'first_name', 'middle_name', 'employee_number', 'pk')
    page = Paginator(query, 50).get_page(request.GET.get('page'))
    resident_by_employee = {}
    if lodging_residents is not None:
        residents = lodging_residents.filter(
            employee_id__in=[employee.pk for employee in page.object_list]
        ).select_related('camp', 'bed__building').order_by('arrived_on', 'pk')
        for stay in residents:
            resident_by_employee.setdefault(stay.employee_id, stay)
    items = []
    for employee in page.object_list:
        label = employee.display_name
        if employee.employee_number:
            label += f' · № {employee.employee_number}'
        if employee.position_id:
            label += f' · {employee.position}'
        if not employee.is_active:
            label += ' · архивный'
        item = {'id': employee.pk, 'text': label}
        resident = resident_by_employee.get(employee.pk)
        if lodging_kind == 'CHECKIN' and resident:
            if resident.arrived_on > day:
                item['text'] += f' · уже есть заселение с {resident.arrived_on:%d.%m.%Y}: {resident.camp} · {resident.bed}'
            else:
                item['text'] += f' · уже заселён: {resident.camp} · {resident.bed}'
            item['disabled'] = True
        elif lodging_kind and lodging_kind != 'CHECKIN' and resident is None:
            item['text'] += ' · ещё не заселён'
            item['disabled'] = True
        elif lodging_kind and lodging_kind != 'CHECKIN' and lodging_camp_id and resident.camp_id != lodging_camp_id:
            item['text'] += f' · заселён в другом местонахождении: {resident.camp} · {resident.bed}'
            item['disabled'] = True
        items.append(item)
    return JsonResponse({
        'items': items,
        'page': page.number,
        'pages': page.paginator.num_pages,
        'total': page.paginator.count,
        'has_next': page.has_next(),
    })
