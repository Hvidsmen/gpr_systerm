from django.db import transaction, IntegrityError
from django.http import JsonResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_POST
from core.permissions import require_roles, PLAN_ROLES
from .models import BrigadeGroup, BrigadeMacroGroup
from .forms import BrigadeGroupForm, BrigadeMacroGroupForm

CONFIG = {'group': (BrigadeGroup, BrigadeGroupForm, 'Группы бригад'),
    'macro': (BrigadeMacroGroup, BrigadeMacroGroupForm, 'Макрогруппы бригад')}


class BrigadeCatalogList(View):
    kind = None

    def get(self, request):
        model, form, title = CONFIG[self.kind]
        return render(request, 'resources/brigade_catalog_list.html', {'kind':self.kind, 'bulk_catalog_kind':'brigade_groups' if self.kind == 'group' else 'brigade_macros', 'title':title, 'records':model.objects.filter(company=request.user.company)})


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
                return JsonResponse({'errors': {'name':['Такая запись уже существует.']}}, status=400)
            return JsonResponse({'value':str(row.pk), 'label':row.name}, status=201)
        return JsonResponse({'errors':form.errors}, status=400)
    return view
