from django import forms
from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_POST
from core.permissions import require_roles, PLAN_ROLES
from .models import WorkGroup, MeasurementUnit

DEFAULT_UNITS = [('м','Метр'),('м²','Квадратный метр'),('м³','Кубический метр'),('шт','Штука'),('т','Тонна'),('кг','Килограмм'),('л','Литр'),('ч','Час'),('m','Метр (m)'),('m3','Кубический метр (m3)')]


class CatalogForm(forms.ModelForm):
    def __init__(self, *args, company, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.company = company
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'

    def clean(self):
        data = super().clean()
        key = 'symbol' if isinstance(self.instance, MeasurementUnit) else 'name'
        if data.get(key) and type(self.instance).objects.filter(company=self.instance.company, **{key:data[key]}).exclude(pk=self.instance.pk).exists():
            self.add_error(key, 'Такая запись уже есть в справочнике.')
        return data


class GroupForm(CatalogForm):
    class Meta:
        model = WorkGroup
        fields = ['name']


class UnitForm(CatalogForm):
    class Meta:
        model = MeasurementUnit
        fields = ['symbol', 'name']


CONFIG = {'group': (WorkGroup, GroupForm, 'Группы работ'), 'unit': (MeasurementUnit, UnitForm, 'Единицы измерения')}


def quick_create(kind):
    @require_POST
    def view(request):
        require_roles(request.user, PLAN_ROLES)
        model, form_class, title = CONFIG[kind]
        form = form_class(request.POST, company=request.user.company)
        if form.is_valid():
            try:
                with transaction.atomic():
                    row = form.save()
            except IntegrityError:
                return JsonResponse({'errors': {'__all__': ['Такая запись уже существует.']}}, status=400)
            return JsonResponse({'value': row.symbol if kind == 'unit' else str(row.pk), 'label': str(row)}, status=201)
        return JsonResponse({'errors': form.errors}, status=400)
    return view


class CatalogList(View):
    kind = None

    def get(self, request):
        model, form_class, title = CONFIG[self.kind]
        return render(request, 'works/catalog_list.html', {'title':title, 'records':model.objects.filter(company=request.user.company), 'form':form_class(company=request.user.company)})


class WorkGroupList(CatalogList):
    kind = 'group'


class MeasurementUnitList(CatalogList):
    kind = 'unit'
