"""Company-scoped administrator forms for initial completed work quantities."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.permissions import require_roles, scope_queryset
from apps.projects.models import ConstructionObject
from apps.works.models import ProjectWork
from apps.works.progress import WorkProgressService
from . import work_opening as service
from .history_models import FactRevision


class Selector(forms.Form):
    construction_object = forms.ModelChoiceField(label='Строительный объект', queryset=ConstructionObject.objects.none())
    q = forms.CharField(label='Поиск работы', required=False)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['construction_object'].queryset = scope_queryset(ConstructionObject.objects.all(), user).order_by('name')


class OpeningForm(forms.Form):
    revision = forms.CharField(widget=forms.HiddenInput)
    reason = forms.CharField(label='Основание ввода или исправления', max_length=1000, widget=forms.Textarea(attrs={'rows': 2, 'class': 'form-control'}))

    def __init__(self, *args, work, **kwargs):
        super().__init__(*args, **kwargs)
        self.initial['revision'] = service.revision(work)
        existing = {row.work_item_id: row for row in work.opening_balances.all()}
        first_fact = work.daily_facts.order_by('date').values_list('date', flat=True).first()
        self.rows = []
        items = list(work.items.order_by('sequence', 'pk')) if work.kind == 'COMPOSITE' else [None]
        for item in items:
            key = item.pk if item else None
            suffix = str(key or 'work')
            row = existing.get(key)
            day_name, qty_name = 'date_' + suffix, 'quantity_' + suffix
            self.fields[day_name] = forms.DateField(label='На начало дня', initial=row.date if row else first_fact or timezone.localdate(),
                widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control form-control-sm'}))
            self.fields[qty_name] = forms.DecimalField(label='Выполнено до начала учёта', initial=row.quantity if row else 0,
                min_value=0, max_digits=15, decimal_places=3, localize=True,
                widget=forms.TextInput(attrs={'inputmode': 'decimal', 'class': 'form-control form-control-sm'}))
            self.rows.append({'key': key, 'name': item.name if item else work.name, 'unit': item.unit if item else work.unit,
                'day': self[day_name], 'quantity': self[qty_name]})

    def quantities(self):
        return {row['key']: (self.cleaned_data[row['day'].name], self.cleaned_data[row['quantity'].name]) for row in self.rows}


@login_required
@require_http_methods(['GET'])
def index(request):
    require_roles(request.user, {'ADMIN'})
    selector = Selector(request.GET or None, user=request.user)
    works = []
    if selector.is_bound and selector.is_valid():
        works = ProjectWork.objects.filter(company=request.user.company, section__construction_object=selector.cleaned_data['construction_object'], merged_source__isnull=True)
        if selector.cleaned_data['q']:
            query = selector.cleaned_data['q']
            works = works.filter(Q(name__icontains=query) | Q(items__name__icontains=query)).distinct()
        works = works.select_related('section', 'complex_object').prefetch_related('opening_balances__work_item').order_by('section__name', 'name', 'pk')
    page = Paginator(works, 100).get_page(request.GET.get('page'))
    params = request.GET.copy(); params.pop('page', None)
    return render(request, 'production/work_opening_list.html', {'selector': selector, 'works': page, 'page_obj': page, 'filter_query': params.urlencode()})


@login_required
@require_http_methods(['GET', 'POST'])
def edit(request, pk):
    require_roles(request.user, {'ADMIN'})
    work = get_object_or_404(scope_queryset(ProjectWork.objects.filter(merged_source__isnull=True), request.user), pk=pk)
    form = OpeningForm(request.POST if request.method == 'POST' else None, work=work)
    if request.method == 'POST' and form.is_valid():
        try:
            service.save(request.user, work.pk, form.quantities(), form.cleaned_data['revision'], form.cleaned_data['reason'])
            messages.success(request, 'Входящие остатки сохранены. Дневной факт не изменён.')
            return redirect('production:work_opening_edit', pk=work.pk)
        except ValidationError as exc:
            form.add_error(None, '; '.join(exc.messages))
    return render(request, 'production/work_opening_edit.html', {'work': work, 'form': form,
        'completed': WorkProgressService.completed(work),
        'history': FactRevision.objects.filter(company=request.user.company,
            entity_type='production.workopeningbalance', object_id=work.section.construction_object_id,
            entity_id__in=[str(pk) for pk in work.opening_balances.values_list('pk', flat=True)]).select_related('actor')[:30]},
        status=400 if request.method == 'POST' and form.errors else 200)
