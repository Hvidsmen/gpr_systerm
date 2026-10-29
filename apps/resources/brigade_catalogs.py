from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_POST
from core.permissions import require_roles, PERSONNEL_CATALOG_EDIT_ROLES
from .models import BrigadeGroup, BrigadeMacroGroup
from .forms import BrigadeGroupForm, BrigadeMacroGroupForm

CONFIG = {'group': (BrigadeGroup, BrigadeGroupForm, 'Группы должностей'),
    'macro': (BrigadeMacroGroup, BrigadeMacroGroupForm, 'Макрогруппы должностей')}


class BrigadeCatalogList(View):
    kind = None

    def get(self, request):
        model, form, title = CONFIG[self.kind]
        records = model.objects.filter(company=request.user.company)
        if self.kind=='group':
            from .group_order import ordered
            records=ordered(records)
        if request.GET.get('export') == 'xlsx':
            from core.catalogue_excel import catalogue_response
            return catalogue_response(request, records)
        from .group_order_views import context
        order_context=context(request.user.company,'labor') if self.kind=='group' else {}
        return render(request, 'resources/brigade_catalog_list.html', {**order_context,'kind':self.kind, 'bulk_catalog_kind':'brigade_groups' if self.kind == 'group' else 'brigade_macros', 'title':title, 'records':records})

    def post(self,request):
        if self.kind!='group':
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        from .group_order_views import save
        return save(request,'labor')


def quick_create(kind):
    @require_POST
    def view(request):
        require_roles(request.user, PERSONNEL_CATALOG_EDIT_ROLES)
        model, form_class, title = CONFIG[kind]
        form = form_class(request.POST, company=request.user.company)
        if form.is_valid():
            try:
                with transaction.atomic():
                    row = form.save()
            except IntegrityError:
                return JsonResponse({'errors': {'name':['Такая запись уже существует.']}}, status=400)
            return JsonResponse({'value':str(row.pk), 'label':row.name}, status=201)
        return JsonResponse({'errors':form.errors}, status=400)
    return view


class BrigadeGroupUpdate(View):
    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, PERSONNEL_CATALOG_EDIT_ROLES)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, pk):
        from django.shortcuts import get_object_or_404
        row = get_object_or_404(BrigadeGroup, company=request.user.company, pk=pk)
        return render(request, 'resources/brigade_group_form.html', {
            'form': BrigadeGroupForm(company=request.user.company, instance=row),
        })

    def post(self, request, pk):
        from django.shortcuts import get_object_or_404, redirect
        from django.contrib import messages
        with transaction.atomic():
            row = get_object_or_404(BrigadeGroup.objects.select_for_update(), company=request.user.company, pk=pk)
            form = BrigadeGroupForm(request.POST, company=request.user.company, instance=row)
            if form.is_valid():
                try:
                    with transaction.atomic():
                        form.save()
                except IntegrityError:
                    form.add_error('name', 'Такая запись уже есть в справочнике.')
                else:
                    messages.success(request, 'Группа должностей сохранена.')
                    return redirect('resources:brigade_group_list')
        return render(request, 'resources/brigade_group_form.html', {'form': form})
