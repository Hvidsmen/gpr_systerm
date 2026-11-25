"""Portal-like Excel report for the personnel dispatcher roster."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


NAVY = '173C5B'
TEAL = '167A94'
TEAL_DARK = '0B6175'
LIGHT_BLUE = 'EAF3F8'
PALE_BLUE = 'F4F8FB'
ACCENT = 'D9EEF4'
GREEN = 'E2F2E8'
YELLOW = 'FFF1C7'
RED = 'FCE1E3'
GREY = 'E9EEF2'
WHITE = 'FFFFFF'
TEXT = '17324D'
BORDER = Side(style='thin', color='C7D7E3')


def safe_value(value):
    """Prevent spreadsheet formula execution in every user-controlled string."""
    return "'" + value if isinstance(value, str) and value.startswith(('=', '+', '-', '@')) else value


def display_number(value, suffix=''):
    if value is None:
        return '—'
    if hasattr(value, 'to_integral_value') and value == value.to_integral_value():
        value = int(value)
    return f'{value}{suffix}'


def display_date(value):
    return value.strftime('%d.%m.%Y') if value else '—'


def add_card(sheet, row, first, last, label, value, fill=LIGHT_BLUE):
    sheet.merge_cells(start_row=row, start_column=first, end_row=row, end_column=last)
    sheet.merge_cells(start_row=row + 1, start_column=first, end_row=row + 1, end_column=last)
    label_cell = sheet.cell(row, first, safe_value(label))
    value_cell = sheet.cell(row + 1, first, safe_value(value))
    for current in (label_cell, value_cell):
        current.fill = PatternFill('solid', fgColor=fill)
        current.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        current.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
    label_cell.font = Font(size=9, color='52708A')
    value_cell.font = Font(size=16, bold=True, color=NAVY)


def merged_line(sheet, row, value, *, fill=WHITE, color=TEXT, bold=False, size=10, height=None):
    sheet.merge_cells(start_row=row, start_column=1, end_row=row, end_column=12)
    cell = sheet.cell(row, 1, safe_value(value))
    cell.fill = PatternFill('solid', fgColor=fill)
    cell.font = Font(bold=bold, color=color, size=size)
    cell.alignment = Alignment(vertical='center', wrap_text=True)
    cell.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
    if height:
        sheet.row_dimensions[row].height = height
    return cell


def status_fill(row):
    if row.get('departure_warning') == 'overdue' or row.get('arrival_overdue'):
        return RED
    if not row.get('person'):
        return YELLOW
    if row.get('state') == 'MV':
        return GREEN
    if row.get('state') == 'MO':
        return GREY
    if row.get('today_code') == 'ДП':
        return ACCENT
    return PALE_BLUE


def detail_values(row, number):
    person = row.get('person')
    return [
        number,
        person.display_name if person else 'Открытая вакансия',
        person.display_employee_number if person else '',
        row.get('status_caption') or row.get('state_label') or 'Уточнить статус',
        row.get('state_days') if row.get('state_days') is not None else '—',
        str(row.get('assignment_object') or ''),
        str(row.get('camp') or 'Не привязан'),
        display_date(row.get('arrived_on')),
        display_date(row.get('last_departure')),
        display_date(row.get('planned_departure_display')),
        display_date(row.get('planned_return')),
        f"{row.get('schedule_label') or 'Не задан'}\nКод на дату: {row.get('today_code') or 'не задан'}",
    ]


def add_pretty_sheet(book, *, day, groups, summary, summary_stats, selected_camps, filters):
    sheet = book.active
    sheet.title = 'Состав персонала'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = TEAL
    sheet.freeze_panes = 'B12'
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_options.horizontalCentered = True
    sheet.oddFooter.center.text = 'Страница &P из &N'
    sheet.oddFooter.right.text = 'Сформировано порталом'
    sheet.sheet_properties.outlinePr.summaryBelow = True

    sheet.merge_cells('A1:L1')
    title = sheet['A1']
    title.value = 'Диспетчерская персонала · Состав персонала'
    title.fill = PatternFill('solid', fgColor=NAVY)
    title.font = Font(size=18, bold=True, color=WHITE)
    title.alignment = Alignment(vertical='center')
    sheet.row_dimensions[1].height = 32

    project = filters.get('construction_object')
    scope = f"На дату: {day:%d.%m.%Y} · Проект: {project.name if project else 'все'} · Местонахождения: {', '.join(camp.name for camp in selected_camps)}"
    merged_line(sheet, 2, scope, fill=LIGHT_BLUE, bold=True, height=25)
    active_filters = []
    if filters.get('q'):
        active_filters.append(f"поиск: {filters['q']}")
    if filters.get('position'):
        active_filters.append(f"должность: {filters['position']}")
    if filters.get('state'):
        active_filters.append(f"статус: {filters.get('state_label', filters['state'])}")
    merged_line(sheet, 3, 'Фильтры: ' + ('; '.join(active_filters) if active_filters else 'не заданы'), color='52708A')

    add_card(sheet, 4, 1, 2, 'Привязано сотрудников', display_number(summary['assigned']))
    add_card(sheet, 4, 3, 4, 'План СМГ', display_number(summary['current_smg']))
    add_card(sheet, 4, 5, 6, 'На вахте', display_number(summary['on_shift']), GREEN)
    add_card(sheet, 4, 7, 8, 'Выполнение плана', display_number(summary['completion'], '%'), ACCENT)
    add_card(sheet, 4, 9, 10, 'Открытых вакансий', display_number(summary['vacancies']), YELLOW)
    add_card(sheet, 4, 11, 12, 'На МО', display_number(summary['rest']), GREY)
    add_card(sheet, 7, 1, 2, 'Заезжают', display_number(summary['arriving']), ACCENT)
    add_card(sheet, 7, 3, 4, 'Выезжают', display_number(summary['departing']), ACCENT)
    for index, item in enumerate(summary['future_gpr']):
        first = 5 + index * 2
        add_card(sheet, 7, first, first + 1, f"План ГПР · {item['month']:%m.%Y}", display_number(item['value']))
    add_card(sheet, 7, 11, 12, 'График не задан', display_number(summary_stats.get('no_schedule', 0)), YELLOW)

    merged_line(sheet, 10, 'Состав по должностям', fill=TEAL, color=WHITE, bold=True, size=13, height=27)
    row_number = 12
    headers = ['№', 'Сотрудник / вакансия', 'Табельный номер', 'Вахта / отдых', 'Дней', 'Объект', 'Местонахождение', 'Фактический заезд', 'Фактический выезд', 'Выезд по графику', 'Возвращение по графику', 'График']
    current_labor_group = None
    for group in groups:
        if filters.get('roster_view') == 'groups' and group.get('labor_group') != current_labor_group:
            current_labor_group = group.get('labor_group') or 'Без группы'
            merged_line(sheet, row_number, f'Группа должностей · {current_labor_group}', fill=TEAL_DARK, color=WHITE, bold=True, size=12, height=27)
            row_number += 1
        people = group.get('people', 0)
        merged_line(sheet, row_number, f"{group['name']}   ·   Сотрудников: {people} чел.", fill=NAVY, color=WHITE, bold=True, size=12, height=27)
        sheet.row_dimensions.group(row_number, row_number, outline_level=0)
        row_number += 1
        metrics = [
            ('Привязано', group['summary']['assigned']), ('План СМГ', group['summary']['current_smg']),
            ('На вахте', group['summary']['on_shift']), ('Выполнение', display_number(group['summary']['completion'], '%')),
            ('Вакансии', group['summary']['vacancies']), ('Заезжают', group['summary']['arriving']),
            ('Выезжают', group['summary']['departing']), ('На МО', group['summary']['rest']),
        ]
        for column, (label, value) in enumerate(metrics, start=1):
            cell = sheet.cell(row_number, column, safe_value(f'{label}\n{display_number(value)}'))
            cell.fill = PatternFill('solid', fgColor=LIGHT_BLUE if column != 4 else ACCENT)
            cell.font = Font(bold=True, size=9, color=TEXT)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
        future = ' · '.join(f"ГПР {item['month']:%m.%Y}: {display_number(item['value'])}" for item in group['summary']['future_gpr'])
        sheet.merge_cells(start_row=row_number, start_column=9, end_row=row_number, end_column=12)
        gpr = sheet.cell(row_number, 9, future or 'План ГПР не задан')
        gpr.fill = PatternFill('solid', fgColor=PALE_BLUE)
        gpr.font = Font(bold=True, size=9, color=TEXT)
        gpr.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        gpr.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
        sheet.row_dimensions[row_number].height = 38
        row_number += 1

        plans = '; '.join(f"{plan['kind']} · {plan['object'].name}" for plan in group.get('plans', [])) or 'План не задан'
        merged_line(sheet, row_number, plans, fill=PALE_BLUE, color='52708A', height=23)
        row_number += 1
        for column, header in enumerate(headers, start=1):
            cell = sheet.cell(row_number, column, header)
            cell.fill = PatternFill('solid', fgColor=TEAL)
            cell.font = Font(bold=True, color=WHITE, size=9)
            cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            cell.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
        sheet.row_dimensions[row_number].height = 34
        row_number += 1

        first_detail = row_number
        if group.get('rows'):
            for number, item in enumerate(group['rows'], start=1):
                fill = status_fill(item)
                for column, value in enumerate(detail_values(item, number), start=1):
                    cell = sheet.cell(row_number, column, safe_value(value))
                    cell.fill = PatternFill('solid', fgColor=fill if column == 4 else WHITE if number % 2 else PALE_BLUE)
                    cell.font = Font(size=9, color=TEXT, bold=column == 2)
                    cell.alignment = Alignment(vertical='top', wrap_text=True)
                    cell.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
                    if column in (1, 3, 5, 8, 9, 10, 11):
                        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                sheet.row_dimensions[row_number].height = 34
                row_number += 1
        else:
            merged_line(sheet, row_number, 'По этой должности есть план. Сотрудники и вакансии в выбранном составе пока не заведены.', fill=YELLOW, color='7A5A00', height=28)
            row_number += 1
        if row_number > first_detail:
            sheet.row_dimensions.group(first_detail, row_number - 1, outline_level=1, hidden=False)
        row_number += 1

    if not groups:
        merged_line(sheet, row_number, 'По выбранным объектам, местонахождениям и фильтрам сотрудники и вакансии не найдены.', fill=YELLOW, color='7A5A00', height=30)

    widths = [5, 30, 15, 20, 9, 22, 22, 15, 15, 15, 17, 27]
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.print_area = f'A1:L{max(row_number - 1, 11)}'


def add_raw_sheet(book, *, day, rows, days):
    sheet = book.create_sheet('Данные')
    headers = ['Местонахождение', 'Дата', 'Сотрудник / вакансия', 'Табельный номер', 'Должность', 'Вахта / отдых', 'Заезд', 'Выезд', 'Выезд по графику', 'Возвращение по графику', 'Дней на вахте / МО', 'Начало текущей вахты / МО', 'Источник начала периода', 'График', *[value.strftime('%d.%m.%Y') for value in days]]
    sheet.append(headers)
    for row in rows:
        person = row['person']
        iso = lambda value: value.isoformat() if value else ''
        sheet.append([safe_value(value) for value in [
            str(row.get('camp') or ''), str(day), person.display_name if person else 'Вакансия',
            person.display_employee_number if person else '', row['position_label'], row['state_label'],
            iso(row.get('arrived_on')), iso(row.get('last_departure')), iso(row.get('planned_departure_display')),
            iso(row.get('planned_return')), row.get('state_days'), iso(row.get('state_since')),
            row.get('state_days_source', ''), row.get('schedule_label', ''), *row['codes'],
        ]])
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = '8FA9BA'
    for cell in sheet[1]:
        cell.font = Font(bold=True, color=WHITE)
        cell.fill = PatternFill('solid', fgColor=TEAL)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
    sheet.row_dimensions[1].height = 34
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            cell.border = Border(left=BORDER, right=BORDER, top=BORDER, bottom=BORDER)
    for column in range(1, sheet.max_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 16 if column > 14 else 24


def build_rotation_workbook(**context):
    book = Workbook()
    add_pretty_sheet(book, **{key: context[key] for key in ('day', 'groups', 'summary', 'summary_stats', 'selected_camps', 'filters')})
    add_raw_sheet(book, **{key: context[key] for key in ('day', 'rows', 'days')})
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
