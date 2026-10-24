"""Confirmed administrator-only removal of an object's sections."""
import hashlib
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core.permissions import require_roles, scope_queryset
from apps.audit.models import AuditLog
from apps.works.models import ProjectWork, ComplexObject
from apps.works.purge import WorkPurge
from apps.planning.deletion_graph import ROOT_MODELS
from .models import ConstructionObject, Section

SALT = 'projects.sections.purge.v1'


@login_required
@require_http_methods(['GET', 'POST'])
def purge_sections(request, pk, section_pk=None):
    require_roles(request.user, {'ADMIN'})
    obj = get_object_or_404(scope_queryset(ConstructionObject.objects.all(), request.user), pk=pk)
    sections = Section.objects.filter(company=request.user.company, construction_object=obj)
    if section_pk is not None:
        get_object_or_404(sections, pk=section_pk)
        sections = sections.filter(pk=section_pk)
    error = None
    try:
        with transaction.atomic():
            if request.method == 'POST':
                ConstructionObject.objects.select_for_update().get(pk=obj.pk)
                list(sections.select_for_update())
            rows = list(sections.order_by('pk'))
            preview = WorkPurge(request.user, list(ProjectWork.objects.filter(section__in=rows).values_list('pk', flat=True)))
            signature = hashlib.sha256(json.dumps({
                'sections': [(row.pk, row.name, row.code) for row in rows],
                'works': preview.signature(),
                'complex_objects':list(ComplexObject.objects.filter(section__in=rows).order_by('pk').values_list('pk','name')),
            }, sort_keys=True).encode()).hexdigest()
            payload = {'user': request.user.pk, 'company': request.user.company_id,
                       'object': obj.pk, 'section': section_pk, 'signature': signature}
            if request.method == 'POST':
                try:
                    supplied = signing.loads(request.POST.get('selection', ''), salt=SALT, max_age=900)
                except signing.BadSignature:
                    raise ValidationError('Подтверждение устарело. Проверьте список и подтвердите повторно.')
                if supplied != payload:
                    raise ValidationError('Список разделов или связи изменились. Проверьте обновлённый список.')
                if request.POST.get('confirm_related') != 'on':
                    raise ValidationError('Подтвердите удаление разделов, связанных работ, планов и факта.')
                for label, model in ROOT_MODELS.items():
                    list(model.objects.select_for_update().filter(pk__in=preview.graph.ids[label]))
                preview.perform()
                section_ids = [row.pk for row in rows]
                ComplexObject.objects.filter(company=request.user.company,section__in=rows).delete()
                sections.delete()
                AuditLog.objects.create(user=request.user, action='DELETE', entity_type='projects.Section',
                                        entity_id=str(obj.pk), old_value={'section_ids': section_ids},
                                        comment='Удаление разделов объекта со связанными работами, планами и фактом')
                messages.success(request, f'Удалено разделов: {len(rows)}. Связанные работы, планы и факт удалены.')
                return redirect('projects:section_list', pk=obj.pk)
    except (ValidationError, ProtectedError) as exc:
        error = '; '.join(exc.messages) if isinstance(exc, ValidationError) else 'Обнаружены дополнительные связи. Удаление отменено целиком.'
    return render(request, 'projects/section_purge.html', {
        'construction_object': obj, 'sections': rows, 'preview': preview,
        'complex_objects':ComplexObject.objects.filter(company=request.user.company,section__in=rows).order_by('name'),
        'selection': signing.dumps(payload, salt=SALT, compress=True), 'error': error,
    }, status=400 if error else 200)
