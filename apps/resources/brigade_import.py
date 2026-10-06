"""Validate an entire workbook before importing company-scoped brigades."""
from io import BytesIO
from zipfile import ZipFile
from django import forms
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View
from openpyxl import Workbook, load_workbook
from core.permissions import require_roles, PLAN_ROLES
from .models import Brigade, BrigadeGroup, BrigadeMacroGroup

HEADERS = ['Название', 'Группа', 'Макрогруппа', 'Описание', 'Активна', 'Единица измерения']
MAX_ROWS = 5000


class BrigadeImportForm(forms.Form):
    file = forms.FileField(label='Файл Excel (.xlsx)', widget=forms.FileInput(attrs={'class':'form-control', 'accept':'.xlsx'}))

    def clean_file(self):
        file = self.cleaned_data['file']
        if not file.name.lower().endswith('.xlsx'):
            raise forms.ValidationError('Выберите файл .xlsx. Старый формат .xls нужно пересохранить.')
        if file.size > 5 * 1024 * 1024:
            raise forms.ValidationError('Максимальный размер файла — 5 МБ.')
        return file


def text_value(value):
    return '' if value is None else str(value).strip()


def read_rows(file):
    errors, rows = [], []
    try:
        with ZipFile(file) as archive:
            if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                return [], ['Файл слишком большой после распаковки.']
        file.seek(0)
        workbook = load_workbook(file, read_only=True, data_only=False)
    except Exception:
        return [], ['Не удалось прочитать Excel. Проверьте, что это исправный файл .xlsx без пароля.']
    try:
        sheet = workbook.worksheets[0]
        # Ignore inaccurate worksheet dimensions from third-party exports.
        sheet.reset_dimensions()
        iterator = sheet.iter_rows()
        header = next(iterator, ())
        columns = {text_value(cell.value).casefold(): index for index, cell in enumerate(header)}
        if 'название' not in columns:
            return [], ['В первой строке должен быть заголовок «Название». Скачайте шаблон.']
        recognized = [text_value(cell.value).casefold() for cell in header if text_value(cell.value).casefold() in {h.casefold() for h in HEADERS}]
        if len(recognized) != len(set(recognized)):
            return [], ['Заголовки столбцов не должны повторяться.']
        for number, cells in enumerate(iterator, 2):
            if number > MAX_ROWS + 1:
                errors.append(f'В файле допускается не более {MAX_ROWS} строк данных.')
                break
            if not any(text_value(cell.value) for cell in cells):
                continue
            values = []
            for title in HEADERS:
                index = columns.get(title.casefold())
                cell = cells[index] if index is not None and index < len(cells) else None
                if cell is not None and cell.data_type in ('f', 'e'):
                    errors.append(f'Строка {number}, «{title}»: замените формулу или ошибку Excel обычным значением.')
                values.append(text_value(cell.value) if cell is not None else '')
            name, group, macro, description, active, unit = values
            unit = unit or 'чел.'
            for title, value, limit in [('Название',name,150), ('Группа',group,150), ('Макрогруппа',macro,150), ('Единица измерения',unit,50)]:
                if len(value) > limit:
                    errors.append(f'Строка {number}: «{title}» — не более {limit} символов.')
            if not name:
                errors.append(f'Строка {number}: укажите название.')
            token = active.casefold()
            if token not in ('','да','нет','1','0','true','false','yes','no','активна','неактивна'):
                errors.append(f'Строка {number}: «Активна» должна быть «Да» или «Нет».')
            rows.append((name, group, macro, description, unit, token not in ('нет','0','false','no','неактивна')))
        if not rows and not errors:
            errors.append('В файле нет данных для загрузки.')
        return rows, errors
    except Exception:
        return [], ['Не удалось прочитать строки Excel. Проверьте файл или перенесите данные в шаблон.']
    finally:
        workbook.close()


@transaction.atomic
def import_rows(company, rows):
    from apps.accounts.models import Company
    Company.objects.select_for_update().get(pk=company.pk)
    existing = {name.strip().casefold() for name in Brigade.objects.filter(company=company).values_list('name', flat=True)}
    groups = {row.name.strip().casefold(): row for row in BrigadeGroup.objects.filter(company=company)}
    macros = {row.name.strip().casefold(): row for row in BrigadeMacroGroup.objects.filter(company=company)}
    result = {'created':0, 'skipped':0, 'groups':0, 'macros':0}
    for name, group_name, macro_name, description, unit, active in rows:
        if name.casefold() in existing:
            result['skipped'] += 1
            continue
        selected = []
        for label, catalog, model, key in [(group_name,groups,BrigadeGroup,'groups'), (macro_name,macros,BrigadeMacroGroup,'macros')]:
            row = catalog.get(label.casefold()) if label else None
            if label and row is None:
                row = model.objects.create(company=company, name=label)
                catalog[label.casefold()] = row
                result[key] += 1
            selected.append(row)
        Brigade.objects.create(company=company, name=name, group=selected[0], macro_group=selected[1], description=description, unit=unit, is_active=active)
        existing.add(name.casefold())
        result['created'] += 1
    return result


class BrigadeImport(View):
    def get(self, request):
        require_roles(request.user, PLAN_ROLES)
        return render(request, 'resources/brigade_import.html', {'form':BrigadeImportForm()})

    def post(self, request):
        require_roles(request.user, PLAN_ROLES)
        form = BrigadeImportForm(request.POST, request.FILES)
        errors, result = [], None
        if form.is_valid():
            rows, errors = read_rows(form.cleaned_data['file'])
            if not errors:
                result = import_rows(request.user.company, rows)
        return render(request, 'resources/brigade_import.html', {'form':form, 'errors':errors[:100], 'error_count':len(errors), 'result':result}, status=400 if errors or form.errors else 200)


def brigade_import_template(request):
    require_roles(request.user, PLAN_ROLES)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Бригады'
    sheet.append(HEADERS)
    sheet.freeze_panes = 'A2'
    for column, width in [('A',40),('B',30),('C',30),('D',60),('E',16),('F',24)]:
        sheet.column_dimensions[column].width = width
    guide = workbook.create_sheet('Инструкция')
    for line in [
        'Заполните первый лист. Заголовки первой строки не изменяйте.',
        'Название обязательно. Группа, макрогруппа и описание необязательны.',
        'Новые группы и макрогруппы создаются автоматически в вашей компании.',
        'Единица измерения по умолчанию: чел. Код присваивается автоматически. Активна: Да или Нет, по умолчанию Да.',
        'Существующие названия и повторы в файле пропускаются без обновления.',
        'При ошибке в любой строке весь импорт отменяется. Не более 5000 строк.',
    ]:
        guide.append([line])
    guide.column_dimensions['A'].width = 110
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    response = HttpResponse(output.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="brigades_template.xlsx"'
    return response
