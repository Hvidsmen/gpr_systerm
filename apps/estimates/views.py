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
from .report import build_report


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
    docs = list(docs.prefetch_related('rows'))
    for doc in docs:
        report = build_report(list(doc.rows.values('source_row','kind','parent_row','values')))
        doc.base_total = report['base']; doc.current_total = report['current']
    totals = {key: sum((getattr(d, field) for d in docs), 0) if all(getattr(d, field) is not None for d in docs) else None for key, field in [('base','base_total'),('current','current_total')]}
    return render(request, 'estimates/list.html', {'can_import': role_code(request.user) in PLAN_ROLES, 'documents': docs, 'totals': totals})


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
    report = build_report(list(doc.rows.values('source_row','kind','parent_row','values')))
    return render(request,'estimates/detail.html',{'document':doc,'report':report,'can_delete':role_code(request.user) in PLAN_ROLES})


def catalog_list(request):
    require_roles(request.user,READ_ROLES)
    kind=request.GET.get('kind','work')
    if kind not in {'work','resource','unit'}: kind='work'
    from django.core.paginator import Paginator
    rows=EstimateCatalog.objects.filter(company=request.user.company,kind=kind).order_by('name')
    if request.GET.get('q'):rows=rows.filter(name__icontains=request.GET['q'])
    if request.GET.get('export') == 'xlsx':
        from core.catalogue_excel import catalogue_response
        return catalogue_response(request, rows)
    return render(request,'estimates/catalog.html',{'page':Paginator(rows,100).get_page(request.GET.get('page')),'kind':kind})


def selected_documents(request):
    docs = EstimateDocument.objects.filter(company=request.user.company).select_related('estimate_object').prefetch_related('rows').order_by('estimate_object__name','pk')
    obj = request.GET.get('object')
    if obj:
        get_object_or_404(EstimateObject, company=request.user.company, pk=obj if obj.isdigit() else None)
        docs = docs.filter(estimate_object_id=obj)
    if request.GET.get('id'):
        raw = request.GET['id']
        doc = get_object_or_404(docs, pk=raw if raw.isdigit() else None)
        docs = [doc]
    return docs


def document_data(doc):
    return dict(filename=doc.filename,title=doc.title,object_name=doc.estimate_object.name,sheet=doc.sheet,base_total=doc.base_total,current_total=doc.current_total,warnings=doc.warnings,rows=[dict(source_row=r.source_row,section=r.section,kind=r.kind,parent_row=r.parent_row,values=r.values) for r in doc.rows.all()])


def estimate_export(request):
    require_roles(request.user,READ_ROLES)
    mode=request.GET.get('mode','full')
    if mode not in {'full','works'}:
        from django.http import HttpResponseBadRequest
        return HttpResponseBadRequest('Неизвестный вид выгрузки.')
    data=[document_data(d) for d in selected_documents(request)]
    response=HttpResponse(export(data,mode=mode),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition']=f'attachment; filename="estimates_{mode}.xlsx"'
    return response


def estimate_report_list(request):
    require_roles(request.user,READ_ROLES)
    groups={}
    for doc in selected_documents(request):
        group=groups.setdefault(doc.estimate_object_id,dict(name=doc.estimate_object.name,documents=[]))
        group['documents'].append(dict(document=doc,report=build_report(document_data(doc)['rows'])))
    def total(items,key):
        vals=[item[key] for item in items]
        return sum(vals,0) if all(v is not None for v in vals) else None
    for group in groups.values():
        for key in ['base','current']:
            group[key]=total([d['report'] for d in group['documents']],key)
    totals={key:total(list(groups.values()),key) for key in ['base','current']}
    return render(request,'estimates/report.html',dict(groups=list(groups.values()),totals=totals,objects=EstimateObject.objects.filter(company=request.user.company).order_by('name'),selected_object=request.GET.get('object','')))


from django.views.decorators.http import require_http_methods


@require_http_methods(['GET', 'POST'])
def estimate_delete(request, pk):
    require_roles(request.user, PLAN_ROLES)
    doc = get_object_or_404(EstimateDocument, company=request.user.company, pk=pk)
    if request.method == 'POST':
        with transaction.atomic():
            doc.delete()
        messages.success(request, 'Смета удалена. Справочники сохранены.')
        return redirect('estimates:estimate_list')
    return render(request, 'estimates/delete.html', {'document': doc})


@require_http_methods(['POST'])
def estimate_bulk_delete(request):
    require_roles(request.user, PLAN_ROLES)
    from django.http import HttpResponseBadRequest, Http404
    raw = request.POST.getlist('selected')
    if not raw:
        messages.warning(request, 'Выберите хотя бы одну смету.')
        return redirect('estimates:estimate_list')
    if len(raw) > 5000 or any(not value.isascii() or not value.isdigit() or len(value) > 18 for value in raw):
        return HttpResponseBadRequest('Некорректный список смет.')
    ids = {int(value) for value in raw}
    with transaction.atomic():
        docs = list(EstimateDocument.objects.select_for_update().filter(company=request.user.company, pk__in=ids).select_related('estimate_object').order_by('pk'))
        if len(docs) != len(ids):
            raise Http404('Одна из выбранных смет не найдена.')
        if request.POST.get('confirm') == 'yes':
            EstimateDocument.objects.filter(company=request.user.company, pk__in=ids).delete()
            messages.success(request, f'Удалено смет: {len(docs)}. Справочники сохранены.')
            return redirect('estimates:estimate_list')
    return render(request, 'estimates/bulk_delete.html', {'documents': docs})
