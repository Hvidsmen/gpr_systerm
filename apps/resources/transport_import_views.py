from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.shortcuts import render,get_object_or_404,redirect
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.permissions import require_roles
from apps.production.transport import OPERATORS
from . import transport_import as service
from .transport_import_models import TransportImportBatch,TransportImportRow


class UploadForm(forms.Form):
    date=forms.DateField(label='Дата сводки',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    first=forms.FileField(label='Первый файл · .xlsx / .xlsm')
    second=forms.FileField(label='Второй файл · .xlsx / .xlsm',required=False)


class CorrectionForm(forms.Form):
    version=forms.CharField(widget=forms.HiddenInput)
    def __init__(self,*args,row,**kwargs):
        super().__init__(*args,**kwargs)
        labels=dict(zip(service.FIELDS,service.HEADERS))
        labels.update(site='ВЖГ',planning_type='Техника для планирования',condition='Техническое состояние',repair_status='Работа / ожидание ремонта за день',in_gpr='Участвует в ГПР · Да / Нет')
        for name in service.FIELDS[1:]:
            self.fields[name]=forms.CharField(label=labels[name],required=name in {'model','planning_type','site','condition','repair_status'},
                max_length=10000,widget=forms.Textarea(attrs={'rows':2}) if name in {'defects','request_details','request_status','parts_status','maintenance','comment'} else forms.TextInput())
        self.initial.update(row.data,version=row.updated_at.isoformat())


@login_required
@require_http_methods(['GET','POST'])
def upload(request):
    require_roles(request.user,OPERATORS)
    form=UploadForm(request.POST if request.method=='POST' else None,request.FILES if request.method=='POST' else None)
    if request.method=='POST' and form.is_valid():
        try:batch=service.preview(request.user,[f for f in (form.cleaned_data['first'],form.cleaned_data['second']) if f],form.cleaned_data['date'])
        except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
        else:return redirect('resources:transport_import_review',pk=batch.pk)
    return render(request,'resources/transport_import.html',{'form':form,'title':'Загрузить ежедневные сводки техники'})


@login_required
@require_http_methods(['GET','POST'])
def review(request,pk):
    require_roles(request.user,OPERATORS)
    batch=get_object_or_404(TransportImportBatch,pk=pk,company=request.user.company)
    error=''
    if request.method=='POST':
        try:service.apply(request.user,batch)
        except ValidationError as exc:error='; '.join(exc.messages)
        else:
            messages.success(request,'Импорт завершён. Сведения загружены в ведение техники; новые машины добавлены в справочник. Ошибки сохранены для исправления диспетчером.')
            return redirect('resources:transport_import_review',pk=batch.pk)
    rows=batch.rows.select_related('equipment','duplicate_of')
    return render(request,'resources/transport_import_review.html',{'batch':batch,'page_obj':Paginator(rows,50).get_page(request.GET.get('page')),
        'ready':rows.filter(status='READY').count(),'applied':rows.filter(status='APPLIED').count(),'pending':rows.filter(status='PENDING').count(),
        'error':error,'title':'Предпросмотр импорта' if not batch.applied_at else 'Результат импорта'})


@login_required
@require_http_methods(['GET','POST'])
def errors(request):
    require_roles(request.user,OPERATORS)
    if request.method=='POST':
        count=service.retry_pending(request.user)
        messages.success(request,f'В ведение загружено записей: {count}. Исправлена обработка формул; противоречия, повторы и ячейки без результата остались для проверки.')
        return redirect('resources:transport_import_errors')
    rows=TransportImportRow.objects.filter(company=request.user.company,status='PENDING',batch__applied_at__isnull=False).select_related('batch','duplicate_of').order_by('pk')
    if request.GET.get('q'):rows=rows.filter(error__icontains=request.GET['q'])
    if request.GET.get('export')=='xlsx':
        from io import BytesIO
        from openpyxl import Workbook
        from django.http import HttpResponse
        book=Workbook();sheet=book.active;sheet.title='Ошибки импорта';sheet.append(['Файл','Лист','Строка','Дата','Ошибка',*service.HEADERS])
        for row in rows:
            sheet.append([row.source_file,row.source_sheet,row.source_row,str(row.date),row.error,*[row.data.get(n,'') for n in service.FIELDS]])
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value,str):cell.data_type='s'
        sheet.freeze_panes='F2';sheet.auto_filter.ref=sheet.dimensions;out=BytesIO();book.save(out)
        response=HttpResponse(out.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']='attachment; filename="transport-import-errors.xlsx"';return response
    return render(request,'resources/transport_import_errors.html',{'page_obj':Paginator(rows,30).get_page(request.GET.get('page')),'total':rows.count(),'query':request.GET.get('q','')})


@login_required
@require_http_methods(['GET','POST'])
def correction(request,pk):
    require_roles(request.user,OPERATORS)
    row=get_object_or_404(TransportImportRow.objects.select_related('batch','duplicate_of'),pk=pk,company=request.user.company,batch__applied_at__isnull=False)
    form=CorrectionForm(request.POST if request.method=='POST' else None,row=row)
    if request.method=='POST':
        close=request.POST.get('action')=='duplicate'
        if close or form.is_valid():
            try:
                data={name:form.cleaned_data.get(name,'') for name in service.FIELDS[1:]} if not close else row.data
                service.resolve(request.user,row,data,request.POST.get('version',''),close_duplicate=close)
            except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
            else:
                messages.success(request,'Повтор закрыт. Исходная строка сохранена.' if close else 'Исправленная машина добавлена в справочник и ведение техники.')
                return redirect('resources:transport_import_errors')
    from apps.production.history_models import FactRevision
    from apps.production.history_views import changes
    revisions=FactRevision.objects.filter(company=request.user.company,entity_type='resources.transportimportrow',entity_id=str(row.pk))[:30]
    for revision in revisions:revision.changes=changes(revision)
    return render(request,'resources/transport_import_correction.html',{'row':row,'form':form,'revisions':revisions})
