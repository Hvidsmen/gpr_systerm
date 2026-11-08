"""Controlled catalogue extension from the production fact form."""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from core.fact_edit_window import require_fact_day
from core.permissions import PRODUCTION_DISPATCHERS, require_roles
from apps.planning.models import LoadProfile, LoadProfileItem
from apps.projects.models import ConstructionObject
from apps.works.models import MeasurementUnit, ProjectWork, ProjectWorkItem


@login_required
@require_POST
def create(request):
    require_roles(request.user, PRODUCTION_DISPATCHERS | {'ADMIN'})
    from django import forms
    try:
        day = forms.DateField().clean(request.POST.get('date'))
    except ValidationError as exc:
        return JsonResponse({'error': '; '.join(exc.messages)}, status=400)
    try:
        require_fact_day(request.user, day)
    except ValidationError as exc:
        return JsonResponse({'error': '; '.join(exc.messages)}, status=400)

    obj = get_object_or_404(ConstructionObject, pk=request.POST.get('construction_object'), company=request.user.company)
    from .edit_lock import require_available
    try:require_available(request.user,obj,day,request.POST)
    except ValidationError as exc:return JsonResponse({'error':'; '.join(exc.messages)},status=409)
    work = get_object_or_404(
        ProjectWork.objects.select_related('section', 'complex_object'),
        pk=request.POST.get('project_work'), company=request.user.company,
        section__construction_object=obj, kind=ProjectWork.Kind.COMPOSITE,
        complex_object__allow_dispatcher_work_items=True,
    )
    name = ' '.join(request.POST.get('name', '').split())
    unit = request.POST.get('unit', '').strip()
    new_unit_symbol = ' '.join(request.POST.get('new_unit_symbol', '').split())
    new_unit_name = ' '.join(request.POST.get('new_unit_name', '').split())
    if not name:
        return JsonResponse({'error': 'Укажите название работы.'}, status=400)
    if len(name) > 255:
        return JsonResponse({'error': 'Название работы не должно превышать 255 символов.'}, status=400)
    if work.items.filter(name__iexact=name).exists():
        return JsonResponse({'error': 'В этом комплексе уже есть работа с таким названием.'}, status=400)
    if new_unit_symbol or new_unit_name:
        if not new_unit_symbol or not new_unit_name:
            return JsonResponse({'error': 'Для новой единицы укажите обозначение и название.'}, status=400)
        if len(new_unit_symbol) > 50 or len(new_unit_name) > 150:
            return JsonResponse({'error': 'Слишком длинное обозначение или название единицы.'}, status=400)
    elif not unit:
        return JsonResponse({'error': 'Выберите единицу измерения или создайте новую.'}, status=400)

    try:
        with transaction.atomic():
            if new_unit_symbol:
                measurement, unit_created = MeasurementUnit.objects.get_or_create(
                    company=request.user.company, symbol=new_unit_symbol,
                    defaults={'name': new_unit_name},
                )
            else:
                measurement = MeasurementUnit.objects.get(company=request.user.company, symbol=unit)
                unit_created = False
            profile, _ = LoadProfile.objects.get_or_create(
                company=request.user.company, code='dispatcher-other',
                defaults={'name': 'Равномерно · оперативные работы', 'description': 'Профиль для работ вне СМГ, созданных при вводе факта.'},
            )
            LoadProfileItem.objects.get_or_create(company=request.user.company, profile=profile, workday_number=1, defaults={'percentage': 100})
            item = ProjectWorkItem(
                company=request.user.company, project_work=work, name=name,
                unit=measurement.symbol, load_profile=profile, quantity_per_unit=1, weight=0,
                sequence=(work.items.order_by('-sequence').values_list('sequence', flat=True).first() or 0) + 1,
            )
            # This extension is deliberately allowed only for the explicitly enabled
            # operational object, even when the package already has historical facts.
            item._allow_structure_merge = True
            item.full_clean()
            item.save()
    except MeasurementUnit.DoesNotExist:
        return JsonResponse({'error': 'Выберите единицу измерения из справочника.'}, status=400)
    except IntegrityError:
        return JsonResponse({'error': 'Единица или работа с таким названием уже существует. Обновите страницу.'}, status=400)
    except ValidationError as exc:
        return JsonResponse({'error': '; '.join(exc.messages)}, status=400)

    path = ' → '.join([work.section.name, work.complex_object.name])
    label = f'{work.complex_object.name} → {work.name} → {item.name}'
    return JsonResponse({'unit': {'symbol': measurement.symbol, 'label': str(measurement), 'created': unit_created}, 'choice': {
        'token': f'item:{work.pk}:{item.pk}', 'work': work.pk, 'item': item.pk,
        'label': label, 'unit': item.unit, 'title': item.name, 'kind': 'COMPOSITE',
        'object': work.complex_object.name, 'package': work.name, 'legacy': False,
        'section_id': work.section_id, 'section': work.section.name,
        'complex_id': work.complex_object_id, 'complex': work.complex_object.name,
        'display': {'title': item.name, 'path': path, 'unit': item.unit, 'has_plan': False,
                    'plan': None, 'previous': 0, 'plan_before': None, 'deviation': None},
    }})
