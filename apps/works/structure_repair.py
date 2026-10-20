"""Explicit selected-record repair using the existing historical merge lineage."""
from collections import defaultdict
import hashlib, json
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views import View
from core.permissions import require_roles, PLAN_ROLES
from .models import ProjectWork
from .merge_views import MergeForm
from .merge_service import source_works, preview, fingerprint, apply_merge

SALT = 'work-structure-repair-v1'


def selected_groups(user, ids):
    try:
        ids = set(map(int, ids))
    except (TypeError, ValueError):
        raise ValidationError('Неверный выбор работ.')
    if not ids or len(ids) > 100:
        raise ValidationError('Выберите от 1 до 100 записей.')
    records = list(ProjectWork.objects.filter(company=user.company, pk__in=ids).select_related('section').order_by('pk'))
    if len(records) != len(ids):
        raise ValidationError('Часть работ недоступна.')
    groups = defaultdict(list)
    for work in records:
        groups[(work.section.construction_object_id, work.name.strip().casefold())].append(work.pk)
    return [source_works(user, group, minimum=1) for group in groups.values()]


def repair_fingerprint(works):
    return hashlib.sha256(json.dumps({'data':fingerprint(works), 'sections':[(work.section_id, work.section.name) for work in works]}, sort_keys=True).encode()).hexdigest()


class RepairForm(MergeForm):
    def __init__(self, *args, works, **kwargs):
        super().__init__(*args, works=works, **kwargs)
        self.object = works[0].section.construction_object
        self.fields['section'].required = False
        self.fields['section'].help_text = 'Выберите существующий раздел или укажите название нового ниже.'
        self.fields['new_section_name'] = forms.CharField(label='Создать новый раздел', required=False, max_length=255, help_text='Новый раздел будет создан на этом же объекте при подтверждении. Поле «Раздел» оставьте пустым.', widget=forms.TextInput(attrs={'class':'form-control', 'placeholder':'Название нового раздела'}))
        self.order_fields(['name', 'section', 'new_section_name', *self.fields.keys()])
        for work in works:
            self.fields[f'item_name_{work.pk}'] = forms.CharField(label=f'Работа в составе комплекса (запись №{work.pk})', max_length=255, initial=work.section.name, widget=forms.TextInput(attrs={'class':'form-control'}))

    def clean(self):
        data = super().clean()
        name = data.get('new_section_name', '')
        section = data.get('section')
        if name and section:
            self.add_error('new_section_name', 'Выберите существующий раздел или создайте новый — одно из двух.')
        elif not name and not section:
            self.add_error('section', 'Выберите раздел или введите название нового.')
        elif name:
            from apps.projects.models import Section
            if Section.objects.filter(company=self.object.company, construction_object=self.object, name__iexact=name).exists():
                self.add_error('new_section_name', 'Такой раздел уже существует на этом объекте. Выберите его из списка.')
            else:
                data['section'] = Section(company=self.object.company, construction_object=self.object, name=name)
        return data


def make_forms(user, groups, data=None):
    return [RepairForm(data, prefix=f'g{index}', company=user.company, works=works, initial={'name':works[0].name, 'section':None, 'unit':works[0].unit, 'allow_fractional':True, 'work_group':works[0].work_group_id}) for index, works in enumerate(groups)]


class StructureRepairView(View):
    def get(self, request):
        require_roles(request.user, PLAN_ROLES)
        return redirect('works:work_list')

    def post(self, request):
        require_roles(request.user, PLAN_ROLES)
        try:
            if request.POST.get('stage') == 'confirm':
                payload = signing.loads(request.POST.get('preview',''), salt=SALT, max_age=3600)
                if payload['actor'] != request.user.pk or payload['company'] != request.user.company_id:
                    raise signing.BadSignature
                with transaction.atomic():
                    from apps.projects.models import ConstructionObject
                    selected = list(ProjectWork.objects.select_for_update().filter(company=request.user.company, pk__in=payload['ids']).values_list('section__construction_object_id', flat=True))
                    list(ConstructionObject.objects.select_for_update().filter(company=request.user.company, pk__in=selected).order_by('pk'))
                    groups = selected_groups(request.user, payload['ids'])
                    forms_list = make_forms(request.user, groups, payload['data'])
                    if not all(form.is_valid() for form in forms_list):
                        raise ValidationError('Настройки изменились. Выполните просмотр заново.')
                    current = [repair_fingerprint(works) for works in groups]
                    if current != payload['fingerprints']:
                        raise ValidationError('Работы, планы или факт изменились. Выполните просмотр заново.')
                    revisions = 0
                    new_sections = {}
                    for works, form in zip(groups, forms_list):
                        section = form.cleaned_data['section']
                        if section.pk is None:
                            key = (section.construction_object_id, section.name.casefold())
                            if key not in new_sections:
                                section.full_clean()
                                section.save()
                                new_sections[key] = section
                            form.cleaned_data['section'] = new_sections[key]
                        _, created = apply_merge(request.user, [w.pk for w in works], form.cleaned_data, fingerprint(works), minimum=1)
                        revisions += len(created)
                messages.success(request, f'Структура исправлена. Создано комплексов: {len(groups)}. Новых черновиков планов: {revisions}; проверьте и согласуйте их.')
                return redirect('works:work_list')
            groups = selected_groups(request.user, request.POST.getlist('selected'))
            configuring = request.POST.get('stage') != 'preview'
            forms_list = make_forms(request.user, groups, None if configuring else request.POST)
            context = {'groups': [{'works':works,'form':form} for works,form in zip(groups,forms_list)], 'ids':[w.pk for works in groups for w in works]}
            if not configuring and all([form.is_valid() for form in forms_list]):
                reports = [preview(request.user, works, form.cleaned_data) for works,form in zip(groups,forms_list)]
                for row, report in zip(context['groups'],reports):
                    row['report'] = report
                context['preview'] = signing.dumps({'actor':request.user.pk,'company':request.user.company_id,'ids':context['ids'],'data':{key:value for key,value in request.POST.items() if key.startswith('g')},'fingerprints':[repair_fingerprint(works) for works in groups]}, salt=SALT, compress=True)
            return render(request,'works/structure_repair.html',context)
        except (signing.BadSignature, KeyError, TypeError, ValueError):
            messages.error(request,'Просмотр устарел или изменён. Выберите записи заново.')
        except ValidationError as exc:
            messages.error(request,'; '.join(exc.messages))
        return redirect('works:work_list')
