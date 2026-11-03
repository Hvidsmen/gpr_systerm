"""HTML and XLSX use the same saved, company/object-scoped monthly comparison."""
from io import BytesIO
from decimal import Decimal
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from core.permissions import READ_ROLES, require_roles, role_code
from .forms import ObjectDateForm
from .entry_concurrency import sqlite_serialized
from .smg_period_report import build
from .report_grouping import work_display_rows, resource_display_rows, day_matrix


def day_people(obj,day):
    from .production_entry import report_data,work_catalogue
    catalogue={row['token']:row['label'] for row in work_catalogue(obj)}
    groups={}
    for person in report_data(obj,day)['people']:
        person['assignment']='Связи не проверены' if not person['reviewed'] else 'Работал без привязки к работе' if person['general'] else '; '.join(catalogue.get(target,target) for target in person['targets'])
        key=person['group']
        group=groups.setdefault(key,{'label':key,'order':person['group_order'],'people':[]})
        group['people'].append(person)
    for group in groups.values():
        group['people'].sort(key=lambda person:(person['position'],person['name'],person['shift']))
        group['count']=len({person['employee'] for person in group['people']})
    return sorted(groups.values(),key=lambda group:(group['order'],group['label']))


def report_for(request):
    require_roles(request.user, READ_ROLES | {'DISPATCHER', 'ALL_FACT_DISPATCHER','MECHANIZATION_DISPATCHER'})
    selector = ObjectDateForm(request.GET, user=request.user,production_read=True)
    if not selector.is_valid():
        return None
    report = build(selector.cleaned_data['construction_object'], selector.cleaned_data['date'], request.GET.get('schedule'), day_only=request.GET.get('scope')=='day', equipment_only=request.GET.get('equipment_only')=='1',include_fuel=role_code(request.user)!='MECHANIZATION_DISPATCHER' and request.GET.get('include_fuel')!='0')
    report['show_days'] = request.GET.get('by_day') == '1'
    if report['show_days']:report['day_matrix']=day_matrix(report)
    if report['day_only'] and not report['equipment_only']:report['people_details_groups']=day_people(report['object'],report['day'])
    return report


@login_required
@require_GET
@never_cache
@sqlite_serialized
def detail(request):
    report = report_for(request)
    if report is None:
        return JsonResponse({'error': 'Выберите доступный объект и дату.'}, status=400)
    if request.GET.get('scope') == 'day':
        return render(request, 'production/_daily_report_equipment.html', report)
    query = {'construction_object': report['object'].pk, 'date': report['day'].isoformat()}
    if report['schedule']:
        query['schedule'] = report['schedule'].pk
    report['show_days'] = request.GET.get('by_day') == '1'
    if report['show_days']:query['by_day'] = '1'
    if report['equipment_only']:query['equipment_only']='1'
    if not report['include_fuel']:query['include_fuel']='0'
    report['export_url'] = reverse('production:smg_period_export') + '?' + urlencode(query)
    return render(request, 'production/_smg_period_report.html', report)


def workbook(report):
    book = Workbook()
    book.remove(book.active)
    source = (f'СМГ · версия {report["schedule"].revision} · {report["schedule"].get_status_display()}'
        if report['schedule'] else 'Нет действующего СМГ. План не определён.')

    def sheet(title, headers, records, fields, note):
        page = book.create_sheet(title)
        page.append([f'{"Сводка за день" if report.get("day_only") else "План / факт за период СМГ"} · {report["object"].name}'])
        page.append([f'{report["start"]:%d.%m.%Y} — {report["end"]:%d.%m.%Y} · {source}'])
        page.append([note])
        page.append(headers)
        for record in records:
            page.append([record.get(field) for field in fields])
            line=page.max_row
            page.row_dimensions[line].outlineLevel=min(record.get('level',0),7)
            if record.get('heading'):
                for cell in page[line]:cell.font=Font(bold=True,color='173F57');cell.fill=PatternFill('solid',fgColor='EDF5FA')
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

    sections=[('equipment','Техника')] if report.get('equipment_only') else [('works','Работы'),('labor','Люди'),('equipment','Техника')]
    for key, title in sections:
        peak=key in {'labor','equipment'} and not report.get('day_only')
        sheet(title, ['Наименование', 'Контекст', 'Ед.', 'План · максимум за день' if peak else 'План СМГ', 'Факт · максимум за день' if peak else 'Сохранённый факт', 'Факт − план', 'Выполнение, %', 'Дней с фактом'],
            work_display_rows(report['work_groups']) if key=='works' else resource_display_rows(report['labor_groups' if key=='labor' else 'equipment_groups']), ['label', 'context', 'unit', 'plan', 'actual', 'deviation', 'percent', 'recorded_days'],
            (f'План и факт в {"чел." if key=="labor" else "ед."} — независимые максимумы за день; даты могут различаться. Итог группы — максимум дневной суммы позиций. ' if peak else '')+
            'Пустая ячейка — нет значения; 0 — сохранённый ноль. Факт за весь период, включая дни после выбранной даты.')
    if not report.get('equipment_only') and report.get('include_fuel',True):
        sheet('ГСМ · план объекта', ['ГСМ', 'Ед.', 'План СМГ'], report['fuel'], ['label', 'unit', 'plan'],
            'План объекта в единицах СМГ (л). Складской расход ВЖГ не распределён по объектам; сравнение не рассчитывается.')
        sheet('ГСМ · факт ВЖГ', ['ВЖГ', 'Ед.', 'Приход за период', 'Расход за период', 'Дней с фактом'],
            report['warehouse'], ['label', 'unit', 'received', 'actual', 'recorded_days'],
            'Общий складской факт связанных ВЖГ в м³. Включает расход на все обслуживаемые ими объекты. Нет записи — нет значения.')
    if report.get('show_days'):
        from datetime import timedelta
        from openpyxl.utils import get_column_letter
        dates=[report['start']+timedelta(days=n) for n in range((report['end']-report['start']).days+1)]
        date_fields=['day_'+str(n) for n in range(len(dates))]

        def matrix(title, identity_headers, identities, records):
            lines=[]
            for record in records:
                values={row['date']:row for row in record['daily_rows']}
                for field, label in [('plan','План'),('actual','Факт'),('deviation','Факт − план'),('percent','Выполнение, %')]:
                    line={name:record.get(name) for name in identities};line['metric']=label
                    for n,day in enumerate(dates):line[date_fields[n]]=values.get(day,{}).get(field)
                    lines.append(line)
            page=sheet(title,[*identity_headers,'Показатель',*dates],lines,[*identities,'metric',*date_fields],
                'Даты расположены по столбцам. План, факт, отклонение и выполнение — отдельными строками. Пустая ячейка означает отсутствие значения.')
            first_date=len(identities)+2
            page.freeze_panes=get_column_letter(first_date)+'5'
            for column in range(first_date,first_date+len(dates)):
                page.column_dimensions[get_column_letter(column)].width=12
                page.cell(4,column).number_format='DD.MM.YYYY'

        if not report.get('equipment_only'):
            works=[]
            for section in report['work_groups']:
                for work in section['works']:
                    for obj in work['objects']:
                        works.append(dict(label=work['label'],section=section['label'],complex=obj['label'],unit=work['unit'],daily_rows=obj['total']['daily_rows']))
            matrix('Работы · дни',['Раздел','Работа','Комплексный объект','Ед.'],['section','label','complex','unit'],works)
        daily_sections=[('equipment_groups','Техника · дни')] if report.get('equipment_only') else [('labor_groups','Люди · дни'),('equipment_groups','Техника · дни')]
        for key,title in daily_sections:
            records=[dict(row,group=group['label']) for group in report[key] for row in group['rows']]
            matrix(title,['Группа','Наименование','Ед.'],['group','label','unit'],records)
    if 'people_details_groups' in report:
        people=[dict(person,group=group['label']) for group in report['people_details_groups'] for person in group['people']]
        sheet('Сотрудники',['Группа','Сотрудник','Должность','Смена','Выполненные работы / занятость'],people,['group','name','position','shift','assignment'],'Сохранённый состав сотрудников и связи с работами за выбранный день.')
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
    filename=(f'Сводный_отчёт_{report["day"]:%Y-%m-%d}.xlsx' if report.get('day_only') else f'Техника_СМГ_{report["start"]:%Y-%m}.xlsx' if report.get('equipment_only') else f'План-факт_СМГ_{report["start"]:%Y-%m}.xlsx')
    return FileResponse(workbook(report), as_attachment=True,
        filename=filename,
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@login_required
@require_GET
@never_cache
@sqlite_serialized
def equipment_page(request):
    require_roles(request.user, READ_ROLES | {'DISPATCHER','MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER'})
    day_only=request.GET.get('scope','period')=='day'
    initial_day=request.GET.get('date') or timezone.localdate().isoformat()
    selector=ObjectDateForm(request.GET if 'construction_object' in request.GET else None,user=request.user,production_read=True,initial={'date':initial_day})
    context={'selector':selector,'day_only':day_only}
    if selector.is_bound and selector.is_valid():
        report=build(selector.cleaned_data['construction_object'],selector.cleaned_data['date'],request.GET.get('schedule'),day_only=day_only,include_fuel=role_code(request.user)!='MECHANIZATION_DISPATCHER')
        report['show_days']=not day_only and request.GET.get('by_day')=='1'
        if report['show_days']:report['day_matrix']=day_matrix(report)
        query={'construction_object':report['object'].pk,'date':report['day'].isoformat(),'include_fuel':'1' if report['include_fuel'] else '0','scope':'day' if day_only else 'period'}
        if report['schedule']:query['schedule']=report['schedule'].pk
        if report['show_days']:query['by_day']='1'
        report['export_url']=reverse('production:smg_period_export')+'?'+urlencode(query)
        context.update(report)
        context['period_label']='за день' if day_only else 'за период'
        if day_only:context['people_details_groups']=day_people(report['object'],report['day'])
        context['day_url']='?'+urlencode({**query,'scope':'day','by_day':'0'})
        context['period_url']='?'+urlencode({**query,'scope':'period','by_day':'0'})
    return render(request,'production/equipment_report.html',context)
