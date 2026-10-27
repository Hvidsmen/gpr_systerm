"""Administrative metadata for existing work records, without planning changes."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.permissions import require_roles
from apps.audit.models import AuditLog
from .models import ProjectWork


@login_required
@require_POST
def legacy_structure_bulk(request):
    require_roles(request.user,{'ADMIN'})
    selected=request.POST.getlist('selected');mode=request.POST.get('legacy_structure')
    if not selected or len(selected)>5000 or any(not value.isdigit() for value in selected) or mode not in {'set','clear'}:
        return HttpResponseBadRequest('Выберите работы и действие с пометкой «Старая структура».')
    ids={int(value) for value in selected};value=mode=='set'
    with transaction.atomic():
        works=list(ProjectWork.objects.select_for_update().filter(company=request.user.company,pk__in=ids,merged_source__isnull=True).order_by('pk'))
        if len(works)!=len(ids):return HttpResponseBadRequest('В списке есть недоступные или архивные работы. Пометки не изменены.')
        changed=[work for work in works if work.is_legacy_structure!=value]
        if changed:
            ProjectWork.objects.filter(company=request.user.company,pk__in=[work.pk for work in changed]).update(is_legacy_structure=value,updated_at=timezone.now())
            AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='works.ProjectWork',entity_id=','.join(str(work.pk) for work in changed)[:100],
                old_value={'is_legacy_structure':not value,'ids':[work.pk for work in changed]},new_value={'is_legacy_structure':value,'ids':[work.pk for work in changed]},comment='Массовая отметка «Старая структура»')
    messages.success(request,f'Пометка {"установлена" if value else "снята"}. Изменено работ: {len(changed)}.')
    query=request.POST.get('return_query','')
    return redirect(reverse('works:work_list')+('?' + query if query else ''))
