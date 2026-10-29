"""Explicitly consolidate selected equipment categories within one company."""
from django.db import transaction
from django.http import Http404
from django.shortcuts import render, redirect
from django.contrib import messages
from django.views import View
from core.permissions import EQUIPMENT_CATALOG_EDIT_ROLES, require_roles
from .models import EquipmentCategory, EquipmentType,Equipment


class EquipmentCategoryMerge(View):
    def post(self, request):
        require_roles(request.user, EQUIPMENT_CATALOG_EDIT_ROLES)
        try:
            ids = {int(value) for value in request.POST.getlist('selected')}
        except (TypeError, ValueError):
            raise Http404('Неверный выбор групп техники.')
        if len(ids) < 2 or len(ids) > 500:
            messages.error(request, 'Выберите от двух до 500 групп техники для объединения.')
            return redirect('resources:equipment_category_list')
        with transaction.atomic():
            groups = list(EquipmentCategory.objects.select_for_update().filter(
                company=request.user.company, pk__in=ids).order_by('name', 'pk'))
            if len(groups) != len(ids):
                raise Http404('Группа техники не найдена.')
            if request.POST.get('merge_stage') == 'confirm':
                try:
                    target_id = int(request.POST.get('target', ''))
                except (TypeError, ValueError):
                    raise Http404('Не выбрана основная группа техники.')
                if target_id not in ids:
                    raise Http404('Основная группа техники должна быть выбрана в списке.')
                target = next(group for group in groups if group.pk == target_id)
                sources = ids - {target_id}
                moved = EquipmentType.objects.filter(company=request.user.company, category_id__in=sources).update(category_id=target_id)
                Equipment.objects.filter(company=request.user.company,catalogue_group_id__in=sources).update(catalogue_group_id=target_id)
                EquipmentCategory.objects.filter(company=request.user.company, pk__in=sources).delete()
                messages.success(request, f'Группы техники объединены в «{target.name}». Перенесено позиций техники: {moved}.')
                return redirect('resources:equipment_category_list')
            return render(request, 'resources/equipment_category_merge.html', {'groups': groups})

from .forms import EquipmentCategoryForm
from django.db import IntegrityError

class EquipmentCategoryUpdate(View):
    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, EQUIPMENT_CATALOG_EDIT_ROLES)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from django.shortcuts import get_object_or_404
        row = get_object_or_404(EquipmentCategory, company=request.user.company, pk=pk)
        return render(request, 'resources/equipment_category_form.html', {
            'form': EquipmentCategoryForm(company=request.user.company, instance=row),
        })

    def post(self, request, pk):
        from django.shortcuts import get_object_or_404, redirect
        from django.contrib import messages
        with transaction.atomic():
            row = get_object_or_404(EquipmentCategory.objects.select_for_update(), company=request.user.company, pk=pk)
            form = EquipmentCategoryForm(request.POST, company=request.user.company, instance=row)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                except IntegrityError:
                    form.add_error('name', 'Такая запись уже есть в справочнике.')
                else:
                    messages.success(request, 'Название группы техники сохранено.')
                    return redirect('resources:equipment_category_list')
        return render(request, 'resources/equipment_category_form.html', {'form': form})
