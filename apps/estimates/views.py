import hashlib
from django import forms
from django.contrib import messages
from django.db import transaction
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from core.permissions import require_roles, READ_ROLES, PLAN_ROLES, role_code
from .models import EstimateObject, EstimateDocument, EstimateRow, EstimateCatalog
from .excel import parse, export


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else [data]
        if not files:
            super().clean(None, initial)
        if len(files) > 50:
            raise forms.ValidationError('Можно загрузить до 50 файлов за один раз.')
        cleaned = [super(MultipleFileField, self).clean(value, initial) for value in files]
        if sum(value.size for value in cleaned) > 100 * 1024 * 1024:
            raise forms.ValidationError('Общий размер файлов не должен превышать 100 МБ.')
        return cleaned


class ImportForm(forms.Form):
    object_name = forms.CharField(label='Объект смет (отдельный справочник)', max_length=255)
    file = MultipleFileField(label='Файлы ЛСР (.xlsx)', widget=MultipleFileInput(attrs={'accept': '.xlsx', 'class': 'form-control'}))


def estimate_list(request):
    require_roles(request.user, READ_ROLES)
    docs = EstimateDocument.objects.filter(company=request.user.company).select_related('estimate_object')
    return render(request, 'estimates/list.html', {'can_import': role_code(request.user) in PLAN_ROLES, 'documents': docs, 'totals': docs.aggregate(base=Sum('base_total'), current=Sum('current_total'))})


def estimate_import(request):
    require_roles(request.user, PLAN_ROLES)
    form = ImportForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            parsed = []
            for upload in form.cleaned_data['file']:
                if not upload.name.lower().endswith('.xlsx'):
                    raise ValueError(f'{upload.name}: требуется файл .xlsx.')
                if upload.size > 12*1024*1024:
                    raise ValueError(f'{upload.name}: размер превышает 12 МБ.')
                try:
                    parsed.append(parse(upload.read(), upload.name))
                except Exception as exc:
                    raise ValueError(f'{upload.name}: не удалось прочитать смету. {exc}') from exc
            added = 0
            skipped = 0
            with transaction.atomic():
                company = request.user.company
                obj, _ = EstimateObject.objects.get_or_create(company=company, name=form.cleaned_data['object_name'].strip())
                for data in parsed:
                    doc, created = EstimateDocument.objects.get_or_create(company=company, estimate_object=obj, digest=data['digest'], defaults={k: data[k] for k in ['filename','title','sheet','base_total','current_total','warnings']})
                    if created:
                        rows=[]
                        for r in data['rows']:
                            catalog=None
                            if r['kind'] in {'item','resource'}:
                                code,name,unit = [str(v or '') for v in r['values'][1:4]]
                                identity=hashlib.sha256(repr((code,name,unit)).encode()).hexdigest()
                                catalog,_=EstimateCatalog.objects.get_or_create(company=company, kind='work' if r['kind']=='item' else 'resource', identity=identity, defaults=dict(code=code,name=name,unit=unit))
                                if unit:
                                    EstimateCatalog.objects.get_or_create(company=company,kind='unit',identity=hashlib.sha256(unit.encode()).hexdigest(),defaults=dict(name=unit,unit=unit))
                            rows.append(EstimateRow(document=doc,catalog=catalog,**r))
                        EstimateRow.objects.bulk_create(rows,batch_size=250)
                    added += int(created)
                    skipped += int(not created)
            messages.success(request, f'Загружено смет: {added}. Уже были загружены: {skipped}.')
            return redirect('estimates:estimate_detail', pk=doc.pk) if len(parsed) == 1 else redirect('estimates:estimate_list')
        except (ValueError, forms.ValidationError) as exc:
            form.add_error('file', str(exc))
    return render(request,'estimates/import.html',{'form':form})


def estimate_detail(request,pk):
    require_roles(request.user,READ_ROLES)
    doc=get_object_or_404(EstimateDocument,company=request.user.company,pk=pk)
    return render(request,'estimates/detail.html',{'document':doc,'rows':doc.rows.all()})


def catalog_list(request):
    require_roles(request.user,READ_ROLES)
    kind=request.GET.get('kind','work')
    if kind not in {'work','resource','unit'}: kind='work'
    from django.core.paginator import Paginator
    rows=EstimateCatalog.objects.filter(company=request.user.company,kind=kind).order_by('name')
    if request.GET.get('q'):rows=rows.filter(name__icontains=request.GET['q'])
    return render(request,'estimates/catalog.html',{'page':Paginator(rows,100).get_page(request.GET.get('page')),'kind':kind})


def estimate_export(request):
    require_roles(request.user,READ_ROLES)
    docs=EstimateDocument.objects.filter(company=request.user.company).prefetch_related('rows')
    if request.GET.get('id'): docs=docs.filter(pk=request.GET['id'])
    data=[]
    for d in docs:
        data.append(dict(filename=d.filename,title=d.title,sheet=d.sheet,base_total=d.base_total,current_total=d.current_total,warnings=d.warnings,rows=list(d.rows.values('source_row','section','kind','parent_row','values'))))
    response=HttpResponse(export(data),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']='attachment; filename="estimates.xlsx"'
    return response
