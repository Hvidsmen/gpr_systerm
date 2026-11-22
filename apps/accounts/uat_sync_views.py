"""Searchable UAT document journals and explicit mapping screens."""
from datetime import date, timedelta, time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core.permissions import require_roles, role_code
from apps.personnel.models import Camp
from apps.projects.models import ConstructionObject, Project
from apps.resources.models import Equipment
from .models import OneCConnection, OneCDiagnosticRun, UatDepartment, UatRepairSheet, UatVehicle, UatWaybill
from .uat_sync import auto_match_vehicles_by_vin, next_scheduled_slot, rebuild_facts, source_ready


READERS = {'ADMIN', 'MECHANIZATION_DISPATCHER', 'ALL_FACT_DISPATCHER', 'PLANNER', 'MANAGER', 'OBSERVER'}


def period(request):
    today = timezone.localdate()
    try:start = date.fromisoformat(request.GET.get('from', ''))
    except ValueError:start = today - timedelta(days=30)
    try:end = date.fromisoformat(request.GET.get('to', ''))
    except ValueError:end = today
    return start, end


def common(request, active):
    return {'uat_tab': active, 'uat_query': request.GET.urlencode()}


@login_required
def waybills(request):
    require_roles(request.user, READERS)
    start, end = period(request);focus = request.GET.get('focus', '')
    focused = UatWaybill.objects.filter(company=request.user.company, pk=focus).first() if focus.isdigit() else None
    if focused:start = end = focused.document_date
    rows = UatWaybill.objects.filter(company=request.user.company, document_date__range=(start, end)).select_related(
        'vehicle__equipment', 'department__project', 'department__construction_object', 'department__camp', 'driver_1', 'driver_2').prefetch_related(
            'tasks__department__project','tasks__department__construction_object','tasks__department__camp','tasks__allocation','tasks__fact')
    if focused:rows=rows.filter(pk=focused.pk)
    department_choices = UatDepartment.objects.filter(company=request.user.company, is_active=True).order_by('name', 'code', 'pk')
    query = ' '.join(request.GET.get('q', '').split())[:200]
    if query:
        rows = rows.filter(Q(number__icontains=query)|Q(vehicle_name__icontains=query)|Q(plate__icontains=query)|Q(vehicle__vin__icontains=query)|Q(vehicle__garage_number__icontains=query)|Q(driver_1_name__icontains=query)|Q(driver_1_number__icontains=query)|Q(driver_2_name__icontains=query)|Q(driver_2_number__icontains=query)|Q(department_name__icontains=query)|Q(object_name__icontains=query)|Q(site_name__icontains=query)|Q(project_name__icontains=query))
    department = request.GET.get('department', '')
    if department.isdigit() and department_choices.filter(pk=department).exists():
        rows = rows.filter(Q(department_id=department)|Q(tasks__department_id=department)).distinct()
    else:
        department = ''
    posted = request.GET.get('posted', '')
    if posted in {'0', '1'}:rows = rows.filter(posted=posted == '1')
    status = request.GET.get('status', '')
    if status in dict(UatWaybill._meta.get_field('fact_status').choices):rows = rows.filter(fact_status=status)
    mapping = request.GET.get('mapping', '')
    if mapping == 'ready':rows = rows.filter(vehicle__equipment__isnull=False).filter(Q(department__project__isnull=False, department__camp__isnull=False)|Q(tasks__department__project__isnull=False, tasks__department__camp__isnull=False)).distinct()
    elif mapping == 'pending':rows = rows.filter(Q(vehicle__equipment__isnull=True)|Q(fact_status__in=['PENDING','PARTIAL','FAILED','CONFLICT'])).distinct()
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    for row in page.object_list:
        row_departments = [task.department for task in row.tasks.all() if task.department_id]
        if row.department_id:row_departments.append(row.department)
        row.mapping_department = next((item for item in row_departments if not item.project_id or not item.camp_id), None)
    context = {**common(request, 'waybills'), 'page': page, 'from_day': start, 'to_day': end, 'q': query, 'department': department, 'departments': department_choices, 'posted': posted, 'status': status, 'mapping': mapping, 'focus':focused.pk if focused else None, 'fact_statuses': UatWaybill._meta.get_field('fact_status').choices}
    return render(request, 'accounts/uat_waybills.html', context)


@login_required
def repairs(request):
    require_roles(request.user, READERS)
    start, end = period(request);rows = UatRepairSheet.objects.filter(company=request.user.company, document_date__range=(start, end)).select_related('vehicle__equipment', 'department__construction_object', 'department__camp')
    query = ' '.join(request.GET.get('q', '').split())[:200]
    if query:rows = rows.filter(Q(number__icontains=query)|Q(vehicle_name__icontains=query)|Q(plate__icontains=query)|Q(vehicle__vin__icontains=query)|Q(vehicle__garage_number__icontains=query)|Q(service_kind__icontains=query)|Q(reason__icontains=query)|Q(master__icontains=query)|Q(department_name__icontains=query))
    posted = request.GET.get('posted', '')
    if posted in {'0', '1'}:rows = rows.filter(posted=posted == '1')
    completed = request.GET.get('completed', '')
    if completed in {'0', '1'}:rows = rows.filter(completed=completed == '1')
    mapping = request.GET.get('mapping', '')
    if mapping == 'ready':rows = rows.filter(vehicle__equipment__isnull=False)
    elif mapping == 'pending':rows = rows.filter(vehicle__equipment__isnull=True)
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    return render(request, 'accounts/uat_repairs.html', {**common(request, 'repairs'), 'page': page, 'from_day': start, 'to_day': end, 'q': query, 'posted': posted, 'completed': completed, 'mapping': mapping})


@login_required
@require_http_methods(['GET','POST'])
def vehicles(request):
    require_roles(request.user, READERS if request.method == 'GET' else {'ADMIN'})
    if request.method == 'POST':
        source = get_object_or_404(UatVehicle, company=request.user.company, pk=request.POST.get('source'))
        equipment = get_object_or_404(Equipment, company=request.user.company, pk=request.POST.get('equipment')) if request.POST.get('equipment') else None
        if equipment and UatVehicle.objects.filter(company=request.user.company, equipment=equipment).exclude(pk=source.pk).exists():messages.error(request, 'Эта техника портала уже связана с другой записью УАТ.')
        else:
            source.equipment = equipment;source.match_status = 'MATCHED' if equipment else 'UNMATCHED';source.match_basis = 'MANUAL' if equipment else ''
            source.save(update_fields=['equipment', 'match_status', 'match_basis', 'updated_at']);rebuild_facts(request.user.company)
            messages.success(request, 'Сопоставление техники сохранено.')
        return redirect('accounts:uat_vehicles')
    automatically_matched = auto_match_vehicles_by_vin(request.user.company)
    if automatically_matched:
        rebuild_facts(request.user.company)
        messages.success(
            request,
            f'По VIN автоматически сопоставлено и сохранено: {automatically_matched}.',
        )
    rows = UatVehicle.objects.filter(company=request.user.company).select_related('equipment__type')
    query = ' '.join(request.GET.get('q', '').split())[:200]
    if query:rows = rows.filter(Q(name__icontains=query)|Q(plate__icontains=query)|Q(vin__icontains=query)|Q(garage_number__icontains=query)|Q(equipment__name__icontains=query)|Q(equipment__plate_number__icontains=query))
    status = request.GET.get('status', '')
    if status in dict(UatVehicle._meta.get_field('match_status').choices):rows = rows.filter(match_status=status)
    page = Paginator(rows, 50).get_page(request.GET.get('page'))
    equipment = Equipment.objects.filter(company=request.user.company).select_related('type').order_by('plate_number','inventory_number','name')
    return render(request, 'accounts/uat_vehicles.html', {**common(request, 'vehicles'), 'page': page, 'q': query, 'status': status, 'statuses': UatVehicle._meta.get_field('match_status').choices, 'equipment_choices': equipment, 'can_map': role_code(request.user) == 'ADMIN'})


@login_required
@require_http_methods(['GET','POST'])
def departments(request):
    require_roles(request.user, READERS if request.method == 'GET' else {'ADMIN'})
    if request.method == 'POST':
        source = get_object_or_404(UatDepartment, company=request.user.company, pk=request.POST.get('source'))
        target = get_object_or_404(ConstructionObject, company=request.user.company, pk=request.POST.get('construction_object')) if request.POST.get('construction_object') else None
        project = get_object_or_404(Project, company=request.user.company, pk=request.POST.get('project')) if request.POST.get('project') else target.project if target else None
        camp = get_object_or_404(Camp, company=request.user.company, pk=request.POST.get('camp')) if request.POST.get('camp') else None
        if not project:messages.error(request, 'Выберите проект.')
        elif target and target.project_id != project.pk:messages.error(request, 'Объект относится к другому проекту.')
        elif camp and not camp.object_links.filter(company=request.user.company, construction_object__project=project).exists():messages.error(request, 'Местонахождение не связано с выбранным проектом.')
        else:
            source.project = project;source.construction_object = target;source.camp = camp
            source.save(update_fields=['project', 'construction_object', 'camp', 'updated_at']);rebuild_facts(request.user.company)
            messages.success(request, 'Связи подразделения УАТ с проектом, объектом и местонахождением сохранены.')
        return redirect(f'{request.path}?focus={source.pk}#uat-department-{source.pk}')
    rows = UatDepartment.objects.filter(company=request.user.company).select_related('project', 'construction_object__project', 'camp')
    query = ' '.join(request.GET.get('q', '').split())[:200]
    if query:rows = rows.filter(Q(name__icontains=query)|Q(code__icontains=query)|Q(project__name__icontains=query)|Q(construction_object__name__icontains=query)|Q(construction_object__project__name__icontains=query)|Q(camp__name__icontains=query))
    focus = request.GET.get('focus', '')
    if focus.isdigit():rows = rows.filter(pk=focus)
    mapping = request.GET.get('mapping', '')
    if mapping == 'ready':rows = rows.exclude(project=None).exclude(camp=None)
    elif mapping == 'pending':rows = rows.filter(Q(project=None)|Q(camp=None))
    page = Paginator(rows, 75).get_page(request.GET.get('page'))
    objects = ConstructionObject.objects.filter(company=request.user.company).select_related('project').order_by('project__name','name')
    camps = Camp.objects.filter(company=request.user.company).order_by('-is_active', 'name', 'pk')
    for camp in camps:
        camp.uat_project_ids = ','.join(str(value) for value in camp.object_links.filter(company=request.user.company).values_list('construction_object__project_id', flat=True).distinct())
    projects = Project.objects.filter(company=request.user.company).order_by('name','pk')
    return render(request, 'accounts/uat_departments.html', {**common(request, 'departments'), 'page': page, 'q': query, 'focus': int(focus) if focus.isdigit() else None, 'mapping': mapping, 'projects': projects, 'objects': objects, 'camps': camps, 'can_map': role_code(request.user) == 'ADMIN'})


@login_required
@require_http_methods(['GET','POST'])
def journal(request):
    require_roles(request.user, READERS if request.method == 'GET' else {'ADMIN'})
    connection, _ = OneCConnection.objects.get_or_create(company=request.user.company)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'schedule':
            enabled = request.POST.get('enabled') == 'on'
            try:clock = time.fromisoformat(request.POST.get('time', '01:00'));lookback = int(request.POST.get('lookback', '7'))
            except (ValueError, TypeError):messages.error(request, 'Укажите корректное время и период.')
            else:
                if not 1 <= lookback <= 366:messages.error(request, 'Период повторного чтения — от 1 до 366 дней.')
                else:
                    connection.uat_sync_enabled = enabled;connection.uat_sync_time = clock;connection.uat_sync_lookback_days = lookback
                    connection.save(update_fields=['uat_sync_enabled','uat_sync_time','uat_sync_lookback_days']);messages.success(request, 'Расписание УАТ сохранено.')
        elif action == 'run':
            if not source_ready(connection):messages.error(request, 'Сначала прочитайте и подтвердите структуру УАТ.')
            else:
                from .one_c import enqueue
                run = enqueue(connection, request.user, database=settings.UAT_DATABASE, uat_import_from=timezone.localdate()-timedelta(days=connection.uat_sync_lookback_days), trust_certificate=True)
                if run and getattr(settings, 'STAGING_CONFIG', None):
                    from .uat import launch_staging_worker
                    launch_staging_worker(run)
                messages.success(request, 'Синхронизация поставлена в очередь.' if run else 'Другое задание уже ожидает или выполняется.')
        else:messages.error(request, 'Неизвестное действие.')
        return redirect('accounts:uat_journal')
    runs = OneCDiagnosticRun.objects.filter(connection=connection, database=settings.UAT_DATABASE, report__uat_import=True).select_related('created_by').order_by('-created_at','-pk')
    page = Paginator(runs, 50).get_page(request.GET.get('page'));last = runs.first()
    last_success = runs.filter(status='DONE').first()
    return render(request, 'accounts/uat_journal.html', {
        **common(request, 'journal'), 'connection': connection, 'page': page,
        'last': last, 'last_success': last_success, 'ready': source_ready(connection),
        'next_run': next_scheduled_slot(connection) if connection.uat_sync_enabled else None,
        'can_manage': role_code(request.user) == 'ADMIN',
    })
