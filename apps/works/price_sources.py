"""Explicit copying of a dated price from another company-scoped work."""
import unicodedata
from datetime import date
from django import forms
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from core.permissions import PLAN_ROLES, require_roles
from .models import ProjectWork
from .prices import price_on


def identity(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().replace('ё', 'е').split())


def source_works(company, exclude=None):
    return ProjectWork.objects.filter(company=company, merged_source__isnull=True).exclude(pk=exclude).select_related('section__construction_object__project').order_by('name', 'pk')


def source_label(work):
    obj = work.section.construction_object
    return f'{work.name} · {work.unit} · {obj.project.name} / {obj.name} / {work.section.name} · №{work.pk}'


def source_field(company, exclude=None):
    field = forms.ModelChoiceField(label='Взять цену из другой работы', required=False,
        queryset=source_works(company, exclude), empty_label='Ввести цену вручную',
        help_text='Выберите работу или найдите совпадения. Единицы измерения должны совпадать. Цена копируется при сохранении; последующие изменения источника не применяются автоматически.')
    field.label_from_instance = source_label
    return field


def source_note(work):
    return f'Цена скопирована из работы №{work.pk}: {source_label(work)}'[:400]


class WorkPriceSources(View):
    def get(self, request):
        require_roles(request.user, PLAN_ROLES)
        try:
            day = date.fromisoformat(request.GET['date']) if request.GET.get('date') else timezone.localdate()
            exclude = int(request.GET['exclude']) if request.GET.get('exclude') else None
            source_id = int(request.GET['source']) if request.GET.get('source') else None
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Некорректная дата или работа.'}, status=400)
        unit, name = identity(request.GET.get('unit', '')), identity(request.GET.get('name', ''))
        if not unit:
            return JsonResponse({'error': 'Сначала выберите единицу измерения.'}, status=400)
        works = source_works(request.user.company, exclude)
        if source_id:
            works = works.filter(pk=source_id)
        elif not name:
            return JsonResponse({'error': 'Сначала укажите название работы.'}, status=400)
        matches = [work for work in works if identity(work.unit) == unit and (source_id or identity(work.name) == name)]
        return JsonResponse({'results': [{'id': work.pk, 'label': source_label(work), 'price': str(price_on(work, day))} for work in matches]})
