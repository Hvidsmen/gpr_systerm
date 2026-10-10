"""Explicitly consolidate selected brigade groups within one company."""
from django.db import transaction
from django.http import Http404
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views import View
from core.permissions import PLAN_ROLES, require_roles
from .models import BrigadeGroup, Brigade


class BrigadeGroupMerge(View):
    def post(self, request):
        require_roles(request.user, PLAN_ROLES)
        try:
            ids = {int(value) for value in request.POST.getlist('selected')}
        except (TypeError, ValueError):
            raise Http404('Неверный выбор групп.')
        if len(ids) < 2 or len(ids) > 500:
            messages.error(request, 'Выберите от двух до 500 групп для объединения.')
            return redirect('resources:brigade_group_list')
        with transaction.atomic():
            groups = list(BrigadeGroup.objects.select_for_update().filter(
                company=request.user.company, pk__in=ids).order_by('name', 'pk'))
            if len(groups) != len(ids):
                raise Http404('Группа не найдена.')
            if request.POST.get('merge_stage') == 'confirm':
                try:
                    target_id = int(request.POST.get('target', ''))
                except (TypeError, ValueError):
                    raise Http404('Не выбрана основная группа.')
                if target_id not in ids:
                    raise Http404('Основная группа должна быть выбрана в списке.')
                target = next(group for group in groups if group.pk == target_id)
                sources = ids - {target_id}
                moved = Brigade.objects.filter(company=request.user.company, group_id__in=sources).update(group_id=target_id)
                BrigadeGroup.objects.filter(company=request.user.company, pk__in=sources).delete()
                messages.success(request, f'Группы объединены в «{target.name}». Перенесено должностей: {moved}.')
                return redirect('resources:brigade_group_list')
            return render(request, 'resources/brigade_group_merge.html', {'groups': groups})
