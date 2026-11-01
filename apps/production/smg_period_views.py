"""HTML and XLSX use the same saved, company/object-scoped monthly comparison."""
from io import BytesIO
from decimal import Decimal
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from core.permissions import READ_ROLES, require_roles
from .forms import ObjectDateForm
from .entry_concurrency import sqlite_serialized
from .smg_period_report import build


def report_for(request):
    require_roles(request.user, READ_ROLES | {'DISPATCHER', 'ALL_FACT_DISPATCHER'})
    selector = ObjectDateForm(request.GET, user=request.user)
    if not selector.is_valid():
        return None
    return build(selector.cleaned_data['construction_object'], selector.cleaned_data['date'], request.GET.get('schedule'))


@login_required
@require_GET
@never_cache
@sqlite_serialized
def detail(request):
    report = report_for(request)
    if report is None:
        return JsonResponse({'error': 'Выберите доступный объект и дату.'}, status=400)
    query = {'construction_object': report['object'].pk, 'date': report['day'].isoformat()}
    if report['schedule']:
        query['schedule'] = report['schedule'].pk
    report['export_url'] = reverse('production:smg_period_export') + '?' + urlencode(query)
    return render(request, 'production/_smg_period_report.html', report)


def workbook(report):
    book = Workbook()
    book.remove(book.active)
    source = (f'СМГ · версия {report["schedule"].revision} · {report["schedule"].get_status_display()}'
        if report['schedule'] else 'Нет действующего СМГ. План не определён.')

    def sheet(title, headers, records, fields, note):
        page = book.create_sheet(title)
        page.append([f'План / факт за период СМГ · {report["object"].name}'])
        page.append([f'{report["start"]:%d.%m.%Y} — {report["end"]:%d.%m.%Y} · {source}'])
        page.append([note])
        page.append(headers)
        for record in records:
            page.append([record.get(field) for field in fields])
        for row in page:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'  # Untrusted catalogue names cannot become formulas.
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if cell.row == 4:
                    cell.font = Font(bold=True, color='173F57')
                    cell.fill = PatternFill('solid', fgColor='EDF5FA')
                elif cell.row > 4 and isinstance(cell.value, (int, float, Decimal)):
                    cell.number_format = '#,##0.###'
        for column in page.columns:
            page.column_dimensions[column[0].column_letter].width = 50 if column[0].column == 1 else 22
        page.freeze_panes = 'B5'
        page.auto_filter.ref = f'A4:{page.cell(max(4,page.max_row),len(headers)).coordinate}'
        return page

    for key, title in [('works', 'Работы'), ('labor', 'Люди'), ('equipment', 'Техника')]:
        sheet(title, ['Наименование', 'Контекст', 'Ед.', 'План СМГ', 'Сохранённый факт', 'Факт − план', 'Выполнение, %', 'Дней с фактом'],
            report[key], ['label', 'context', 'unit', 'plan', 'actual', 'deviation', 'percent', 'recorded_days'],
            'Пустая ячейка — нет значения; 0 — сохранённый ноль. Факт за весь период, включая дни после выбранной даты.')
    sheet('ГСМ · план объекта', ['ГСМ', 'Ед.', 'План СМГ'], report['fuel'], ['label', 'unit', 'plan'],
        'План объекта в единицах СМГ (л). Складской расход ВЖГ не распределён по объектам; сравнение не рассчитывается.')
    sheet('ГСМ · факт ВЖГ', ['ВЖГ', 'Ед.', 'Приход за период', 'Расход за период', 'Дней с фактом'],
        report['warehouse'], ['label', 'unit', 'received', 'actual', 'recorded_days'],
        'Общий складской факт связанных ВЖГ в м³. Включает расход на все обслуживаемые ими объекты. Нет записи — нет значения.')
    output = BytesIO()
    book.save(output)
    output.seek(0)
    return output


@login_required
@require_GET
@never_cache
@sqlite_serialized
def export(request):
    report = report_for(request)
    if report is None:
        return JsonResponse({'error': 'Выберите доступный объект и дату.'}, status=400)
    return FileResponse(workbook(report), as_attachment=True,
        filename=f'План-факт_СМГ_{report["start"]:%Y-%m}.xlsx',
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
