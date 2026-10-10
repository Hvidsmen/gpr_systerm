"""Import meeting schedules into a current version or new object period plans."""

import re

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from zipfile import BadZipFile, ZipFile
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from django.views import View
from openpyxl import load_workbook

from core.permissions import PLAN_ROLES, require_roles
from apps.projects.models import Project, ConstructionObject, Section
from apps.resources.models import (
    Brigade,
    BrigadeGroup,
    EquipmentType,
    EquipmentCategory,
)
from apps.works.models import MeasurementUnit, ProjectWork, ProjectWorkItem
from apps.works.progress import quantity_from_totals, work_specification
from .models import (
    LoadProfile,
    LoadProfileItem,
    WorkMonthAllocation,
    ResourceMonthAllocation,
    GlobalPlanVersion,
)
from .workspace_services import WorkspaceService, months_between

SALT = "planning.meeting-import.v1"
MAX_ROWS = 2500
MAX_COLUMNS = 4096


def clean_text(value):
    return " ".join(str(value or "").split())


def empty_value(value):
    return value is None or isinstance(value, str) and not value.strip()


def clean_unit(value):
    return clean_text(value).rstrip(" .")


def key(value):
    return clean_text(value).casefold().rstrip(" .:-")


def amount(value, count=False):
    try:
        result = Decimal(
            str(value).replace(" ", "").replace("\xa0", "").replace(",", ".")
        )
        if (
            not result.is_finite()
            or result < 0
            or result > (2147483647 if count else Decimal("999999999999.999999"))
        ):
            raise ValueError
        if count and result != result.to_integral_value():
            raise ValueError
        return (
            result
            if count
            else result.quantize(Decimal(".000001"), rounding=ROUND_HALF_UP)
        )
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(
            "Ожидалось неотрицательное целое количество."
            if count
            else "Ожидался неотрицательный объём; проверьте число или формулу Excel."
        )


def parse_sheet(sheet, start, end, resource_rule, facts=False):
    result = {
        "name": clean_text(sheet.title),
        "entries": [],
        "warnings": [],
        "errors": [],
    }
    if sheet.max_row > MAX_ROWS:
        result["errors"].append(f"На листе больше {MAX_ROWS} строк. Разделите файл.")
        return result
    # Styles inflate max_column up to XFD in the supplied workbook. Inspect the
    # date headers first, then read only the actual schedule width.
    last_date_column = 0
    for row in sheet.iter_rows(max_row=min(sheet.max_row, 40), values_only=True):
        dated = [
            j + 1 for j, value in enumerate(row) if isinstance(value, (date, datetime))
        ]
        if len(dated) >= 2:
            last_date_column = max(last_date_column, max(dated))
    width = max(50, last_date_column)
    if width > MAX_COLUMNS or width * sheet.max_row > 2000000:
        result["errors"].append(
            "Слишком много дневных столбцов или ячеек. Разделите лист на несколько периодов."
        )
        return result
    rows = list(sheet.iter_rows(max_row=sheet.max_row, max_col=width, values_only=True))
    headers = None
    dates = {}
    group = "Работы из Excel"
    parent_group = ""
    resource_groups = {"labor": "", "equipment": ""}
    summary = False
    resource_kind = None
    resource_name_col = None
    machine_col = None
    skipped_empty = 0
    skipped_totals = 0

    def add(
        kind, name, unit, value, rownum, source, month, section="", equipment_number=""
    ):
        nonlocal skipped_empty
        if empty_value(value):
            skipped_empty += 1
            return
        try:
            if kind == "equipment" and equipment_number:
                numbers = [part.strip() for part in re.split(r"[,;\n]+", equipment_number) if part.strip()]
                if len(numbers) > 1:
                    warning = (
                        f'Строка {rownum}, «{name}»: перечислено несколько госномеров '
                        f'({equipment_number}). Загружается общее количество техники без отдельного '
                        'госномера; распределение по машинам не задано в файле.'
                    )
                    if warning not in result["warnings"]:
                        result["warnings"].append(warning)
                    equipment_number = ""
                elif numbers:
                    equipment_number = numbers[0]
                if len(equipment_number) > 50:
                    raise ValidationError("Госномер длиннее 50 символов. Укажите один номер либо разделите список запятыми.")
            limit = 255 if kind == "work" else 150
            if (
                not name
                or len(name) > limit
                or len(unit) > 50
                or len(section)
                > (255 if kind == "work" else 150 if kind == "labor" else 100)
                or len(equipment_number) > 50
            ):
                raise ValidationError(
                    f"Название слишком длинное (до {limit} символов), пустое или неверная единица измерения."
                )
            result["entries"].append(
                {
                    "kind": kind,
                    "name": name,
                    "unit": unit,
                    "quantity": str(amount(value, kind != "work")),
                    "row": rownum,
                    "section": section,
                    "source": source,
                    "equipment_number": equipment_number,
                    "month": month.isoformat(),
                }
            )
        except ValidationError as error:
            result["errors"].append(
                f'Строка {rownum}, «{name}»: {"; ".join(error.messages)}'
            )

    previous_plan = None
    for index, row in enumerate(rows):
        row = list(row)
        texts = {
            j: key(v) for j, v in enumerate(row) if isinstance(v, str) and clean_text(v)
        }
        name_col = next(
            (j for j, v in texts.items() if "наименование работ" in v), None
        )
        if name_col is not None:
            previous_plan = None
            headers = {
                "name": name_col,
                "unit": next(
                    (j for j, v in texts.items() if "ед." in v and "изм" in v), None
                ),
                "monthly": next(
                    (j for j, v in texts.items() if "план на месяц" in v), None
                ),
                "marker": next(
                    (j for j, v in texts.items() if v.replace(" ", "") == "план/факт"),
                    None,
                ),
            }
            dates = {}
            resource_kind = None
            resource_name_col = None
            machine_col = None
            continue
        if headers:
            dated = {
                j: v.date() if isinstance(v, datetime) else v
                for j, v in enumerate(row)
                if isinstance(v, (datetime, date))
                and j
                > (
                    headers["marker"]
                    if headers["marker"] is not None
                    else (
                        headers["monthly"]
                        if headers["monthly"] is not None
                        else headers["name"]
                    )
                )
            }
            if len(dated) >= 2:
                selected_dates = [d for d in dated.values() if start <= d <= end]
                if len(selected_dates) != len(set(selected_dates)):
                    result["errors"].append(
                        f"Строка {index+1}: повторные дневные даты внутри периода. Удалите дубли столбцов."
                    )
                dates = dated
                continue
        if not headers:
            continue
        for j, text in texts.items():
            if text.startswith(("людские ресурсы", "технические ресурсы")):
                resource_kind = "labor" if text.startswith("людские") else "equipment"
                resource_name_col = j
            if text.startswith(("гос. номер", "гос.номер")):
                machine_col = j
        marker = key(row[headers["marker"]]) if headers["marker"] is not None else ""
        if facts:
            if marker == "план":
                previous_plan = list(row)
                for j in dates:
                    row[j] = None
            elif marker == "факт":
                if previous_plan is None:
                    result["errors"].append(
                        f"Строка {index+1}: факт без предшествующей строки плана."
                    )
                    continue
                for j in range(headers["marker"]):
                    if row[j] in (None, ""):
                        row[j] = previous_plan[j]
                marker = "план"
                texts = {
                    j: key(v)
                    for j, v in enumerate(row)
                    if isinstance(v, str) and clean_text(v)
                }
            else:
                # Keep headings, but never infer facts from unlabelled daily values.
                for j in dates:
                    row[j] = None
        elif marker == "факт":
            continue
        # Rows for resources have their own merged name and unit columns.
        resource_unit = next(
            (j for j, v in texts.items() if v in {"чел", "ед"} and j > headers["name"]),
            None,
        )
        inferred_plan = (
            headers["marker"] is not None
            and dates
            and resource_kind
            and resource_name_col is not None
            and not marker
            and clean_text(row[resource_name_col])
            and not key(row[resource_name_col]).endswith("ресурсы")
        )
        if (marker == "план" and resource_unit is not None) or inferred_plan:
            if inferred_plan:
                warning = "В ресурсном блоке без меток план/факт первая строка пары трактуется как план. Проверьте строки перед загрузкой."
                if warning not in result["warnings"]:
                    result["warnings"].append(warning)
            kind = (
                ("labor" if texts[resource_unit] == "чел" else "equipment")
                if resource_unit is not None
                else resource_kind
            )
            candidates = [
                j
                for j in texts
                if headers["name"] + 2 < j < (resource_unit or headers["marker"])
                and j != machine_col
                and not texts[j].replace(".", "").isdigit()
            ]
            col = max(candidates) if candidates else None
            if (
                resource_unit is None
                and resource_name_col is not None
                and resource_name_col in candidates
            ):
                col = resource_name_col
            if col is None or not clean_text(row[col]):
                if any(
                    not empty_value(row[j]) and row[j] not in (0, "0")
                    for j, day in dates.items()
                    if start <= day <= end
                ):
                    result["errors"].append(
                        f"Строка {index+1}: заполнен план, но нет названия ресурса."
                    )
                else:
                    skipped_empty += 1
                continue
            name = clean_text(row[col]).rstrip(" :-")
            code = key(row[col - 1]) if col else ""
            # Subtotals (ITR, specialized equipment, etc.) precede their detail
            # rows and must not be counted a second time.
            next_code = ""
            for following in rows[index + 1 :]:
                if key(following[headers["marker"]]) == "план":
                    next_code = key(following[col - 1]) if col else ""
                    break
            subtotal = bool(code and next_code.startswith(code.rstrip(".") + ".")) or (
                clean_text(row[col]).endswith(":")
                and any(word in key(name) for word in ["состав", "персонал", "техника"])
                and bool(next_code)
            )
            if (
                "итого" in key(name)
                or subtotal
                or any(
                    "итого" in text
                    for j, text in texts.items()
                    if j < (resource_unit or headers["marker"])
                )
            ):
                skipped_totals += 1
                if subtotal:
                    resource_groups[kind] = name
                continue
            by_month = defaultdict(list)
            for j, day in dates.items():
                if start <= day <= end:
                    by_month[day.replace(day=1)].append(j)
            if facts:
                for j, day in sorted(dates.items()):
                    if start <= day <= end:
                        add(
                            kind,
                            name,
                            "чел" if kind == "labor" else "ед",
                            row[j],
                            index + 1,
                            "Дневной факт",
                            day,
                            resource_groups[kind],
                            (
                                clean_text(row[machine_col])
                                if machine_col is not None and kind == "equipment"
                                else ""
                            ),
                        )
                continue
            for month, chosen in sorted(by_month.items()):
                try:
                    values = [
                        amount(row[j], True) for j in chosen if not empty_value(row[j])
                    ]
                    value = (
                        (max(values) if resource_rule == "maximum" else values[0])
                        if values
                        else None
                    )
                    if len(set(values)) > 1:
                        result["warnings"].append(
                            f'Строка {index+1}, «{name}», {month:%m.%Y}: количество по дням меняется; выбрано {value} ({"максимум" if resource_rule == "maximum" else "первый заполненный день"}). Оно повторится каждый день месяца в пределах периода плана.'
                        )
                    add(
                        kind,
                        name,
                        "чел." if kind == "labor" else "ед.",
                        value,
                        index + 1,
                        "Дневная строка плана",
                        month,
                        resource_groups[kind],
                        (
                            clean_text(row[machine_col])
                            if machine_col is not None and kind == "equipment"
                            else ""
                        ),
                    )
                except ValidationError as error:
                    result["errors"].append(
                        f'Строка {index+1}, «{name}», {month:%m.%Y}: {"; ".join(error.messages)}'
                    )
            continue
        if marker and marker != "план":
            continue
        name = clean_text(row[headers["name"]])
        if not name or "итого" in key(name):
            continue
        unit = clean_unit(row[headers["unit"]]) if headers["unit"] is not None else ""
        # Numbered parent rows are section totals even when they have units,
        # volumes and no explicit «в том числе» label (e.g. 1 -> 1.1).
        code = key(row[headers["name"] - 1]) if headers["name"] else ""
        next_code = ""
        if code:
            for following in rows[index + 1 :]:
                following_marker = key(following[headers["marker"]]) if headers["marker"] is not None else ""
                if following_marker == "факт":
                    continue
                if clean_text(following[headers["name"]]):
                    next_code = key(following[headers["name"] - 1])
                    break
        numbered_subtotal = bool(code and next_code.startswith(code.rstrip(".") + "."))
        if numbered_subtotal or "в том числе" in key(name):
            group = name.rstrip(" :")
            parent_group = group
            skipped_totals += 1
            continue
        if not unit:
            if len(name) <= 255 and not key(name).startswith(
                ("примечание", "дата", "температура")
            ):
                if code:
                    parent_group = name
                    group = name
                else:
                    group = f"{parent_group} / {name}" if parent_group else name
            continue
        # Only work units from the actual work table, not date/annotation grids.
        if headers["marker"] is not None and marker != "план":
            continue
        if not dates:
            summary = True
            continue
        by_month = defaultdict(list)
        for j, day in dates.items():
            if start <= day <= end:
                by_month[day.replace(day=1)].append(j)
        if facts:
            for j, day in sorted(dates.items()):
                if start <= day <= end:
                    add(
                        "work",
                        name,
                        unit,
                        row[j],
                        index + 1,
                        "Дневной факт",
                        day,
                        group,
                    )
            continue
        for month, chosen in sorted(by_month.items()):
            try:
                values = [amount(row[j]) for j in chosen if not empty_value(row[j])]
                value = sum(values) if values else None
                add(
                    "work",
                    name,
                    unit,
                    value,
                    index + 1,
                    "Сумма дневного плана",
                    month,
                    group,
                )
            except ValidationError as error:
                result["errors"].append(
                    f'Строка {index+1}, «{name}», {month:%m.%Y}: {"; ".join(error.messages)}'
                )
    if summary:
        result["warnings"].append(
            "Сводная справка без дневных дат: месячные и накопительные показатели не загружаются. Нужен лист с датами дневного плана."
        )
    if dates:
        result["warnings"].append(
            "Дневные периоды на листе: "
            + ", ".join(sorted({d.strftime("%m.%Y") for d in dates.values()}))
            + "."
        )
        result["warnings"].append(
            "Объёмы работ суммируются только по дневным столбцам выбранного периода; дневной график будет сформирован системой по профилям работ. Строки факта не загружаются."
        )
    if skipped_empty:
        result["warnings"].append(
            f"Пропущены пустые плановые значения: {skipped_empty}. Явные нули сохраняются."
        )
    if skipped_totals:
        result["warnings"].append(f"Пропущены итоговые строки: {skipped_totals}.")
    if facts:
        result["warnings"] = [
            w for w in result["warnings"] if "план" not in w.casefold()
        ]
        result["warnings"].append(
            "Загружаются только явно отмеченные строки «факт», по точным датам столбцов. Пустые ячейки пропускаются; нули сохраняются."
        )
    if not result["entries"]:
        result["errors"].append(
            "Не найден заполненный факт в выбранном периоде."
            if facts
            else "Не найден заполненный план работ, людей или техники."
        )
    return result


def parse_meeting_workbook(
    upload, start, end, resource_rule="maximum", object_name=None, facts=False, sheet_name=None
):
    if upload.size > 8 * 1024 * 1024 or not upload.name.lower().endswith(".xlsx"):
        raise ValidationError("Нужен файл .xlsx размером до 8 МБ.")
    try:
        with ZipFile(upload) as archive:
            if (
                len(archive.infolist()) > 3000
                or sum(e.file_size for e in archive.infolist()) > 80 * 1024 * 1024
            ):
                raise ValidationError("Файл Excel слишком большой после распаковки.")
        upload.seek(0)
        workbook = load_workbook(
            upload, read_only=True, data_only=True, keep_links=False
        )
        try:
            if len(workbook.sheetnames) > 100:
                raise ValidationError("В одном файле допускается до 100 листов.")
            sheets = list(workbook)
            if object_name is not None:
                sheets = [
                    sheet for sheet in sheets if key(sheet.title) == key(sheet_name or object_name)
                ]
                if len(sheets) != 1:
                    raise ValidationError(
                        (f"В файле должен быть один лист «{sheet_name}»." if sheet_name else
                         f"В файле должен быть один лист объекта «{object_name}».")
                        + " Проверьте название листа."
                    )
            parsed = [
                parse_sheet(sheet, start, end, resource_rule, facts=facts)
                for sheet in sheets
            ]
            if object_name is not None:
                for sheet in parsed:
                    sheet["source_sheet"] = sheet["name"]
                    sheet["name"] = object_name
            return parsed
        finally:
            workbook.close()
    except (BadZipFile, KeyError, ValueError, OSError) as error:
        raise ValidationError(
            "Не удалось прочитать Excel. Сохраните файл как .xlsx."
        ) from error


def unique_match(candidates, name, description):
    matches = [item for item in candidates if key(item.name) == key(name)]
    if len(matches) > 1:
        raise ValidationError(
            f"{description} «{name}»: найдено несколько совпадений. Уточните названия в справочнике или файле."
        )
    return matches[0] if matches else None


def resolve_sheet(company, project, sheet, target_object=None, require_all=True):
    """No writes: resolve identities and surface ambiguity before confirmation."""
    obj = target_object or unique_match(
        ConstructionObject.objects.filter(company=company, project=project),
        sheet["name"],
        "Объект",
    )
    if target_object and (
        obj.company_id != company.pk
        or obj.project_id != project.pk
        or key(obj.name) != key(sheet["name"])
    ):
        raise ValidationError("Лист не соответствует объекту текущей версии.")
    if not obj:
        raise ValidationError(
            f'Объект «{sheet["name"]}» не найден в выбранном проекте. Лист нельзя загрузить.'
        )
    works = (
        list(
            ProjectWork.objects.filter(
                company=company,
                merged_source__isnull=True,
                section__construction_object=obj,
            )
            .select_related("section")
            .prefetch_related("items")
        )
        if obj
        else []
    )
    children = list(
        ProjectWorkItem.objects.filter(
            company=company, project_work__in=works
        ).select_related("project_work")
    )
    catalogs = {
        "labor": list(Brigade.objects.filter(company=company, merge_source__isnull=True).select_related("group").order_by("pk")),
        "equipment": list(
            EquipmentType.objects.filter(company=company, merge_source__isnull=True).select_related("category").order_by("pk")
        ),
    }
    resolved = []
    seen = {}
    for entry in sheet["entries"]:
        row = dict(entry)
        kind = row["kind"]
        if kind == "work":
            row["unit"] = clean_unit(row["unit"])
            matches = [
                w
                for w in works
                if key(w.name) == key(row["name"]) and key(w.unit) == key(row["unit"])
            ]
            scoped = [w for w in matches if key(w.section.name) == key(row["section"])]
            # An explicit section belongs to the identity; never fall back
            # to a same-named work from another section.
            matches = scoped if row["section"] else matches
            child_matches = [
                c
                for c in children
                if key(c.name) == key(row["name"]) and key(c.unit) == key(row["unit"])
            ]
            scoped_children = [
                c
                for c in child_matches
                if key(c.project_work.section.name) == key(row["section"])
                or key(c.project_work.name) == key(row["section"])
            ]
            child_matches = scoped_children if row["section"] else child_matches
            if len(matches) + len(child_matches) > 1:
                raise ValidationError(
                    f'Строка {row["row"]}: работа/подработа «{row["name"]}» неоднозначна. Уточните раздел или название.'
                )
            target = (
                matches[0] if matches else child_matches[0] if child_matches else None
            )
            if matches and target.kind == "COMPOSITE":
                raise ValidationError(
                    f"«{target.name}»: для составной работы нужны строки всех её подработ, а не объём основной работы."
                )
            row["target_type"] = "item" if child_matches and not matches else "work"
            row["target_id"] = target.pk if target else None
            if row["target_type"] == "work":
                value = Decimal(row["quantity"]).quantize(
                    Decimal(".001"), rounding=ROUND_HALF_UP
                )
                if value > Decimal("999999999999.999"):
                    raise ValidationError(
                        f'Строка {row["row"]}: объём основной работы слишком большой.'
                    )
                row["quantity"] = str(value)
            identity = (
                (kind, row["target_type"], target.pk)
                if target
                else (kind, key(row["section"]), key(row["name"]), key(row["unit"]))
            )
        else:
            matches = [
                item for item in catalogs[kind] if key(item.name) == key(row["name"])
            ]
            group_field = "group" if kind == "labor" else "category"
            scoped = [
                item
                for item in matches
                if key(
                    getattr(item, group_field).name
                    if getattr(item, group_field)
                    else ""
                )
                == key(row["section"])
            ]
            if kind == "equipment":
                target = matches[0] if matches else None
                if len(matches) > 1:
                    warning = (
                        f'Техника «{row["name"]}»: в справочнике {len(matches)} записей с одним названием. '
                        f'Выбрана первая запись: ID {target.pk}, категория «{target.category or "Без категории"}». '
                        'Дубли можно объединить в справочнике видов техники.'
                    )
                    if warning not in sheet["warnings"]:
                        sheet["warnings"].append(warning)
            else:
                target = matches[0] if matches else None
                if len(matches) > 1:
                    warning = (f'Должность «{row["name"]}»: в справочнике {len(matches)} записей с одним названием. '
                        f'Выбрана первая: ID {target.pk}, группа «{target.group or "Без группы"}». '
                        'Дубли можно объединить в справочнике бригад (должностей).')
                    if warning not in sheet["warnings"]: sheet["warnings"].append(warning)
            if target and not target.is_active:
                raise ValidationError(
                    f"Ресурс «{target.name}» неактивен. Активируйте его в справочнике перед загрузкой."
                )
            row["target_id"] = target.pk if target else None
            row["target_type"] = kind
            identity = (
                kind,
                target.pk if target else (key(row["name"]),),
                row.get("equipment_number", ""),
            )
        identity = (*identity, row["month"])
        if identity in seen:
            if kind in {"labor", "equipment"}:
                label = "Должность" if kind == "labor" else "Техника"
                warning = (f'{label} «{row["name"]}»: повторная строка {row["row"]} пропущена. '
                    f'Для периода {row["month"]} используется первое вхождение, строка {seen[identity]}.')
                if warning not in sheet["warnings"]: sheet["warnings"].append(warning)
                continue
            # Different work sections are separate; duplicate resource rows would
            # silently inflate a count. Ask users to fix the source instead.
            raise ValidationError(
                f'Строка {row["row"]}: повторная позиция «{row["name"]}». Разделите или переименуйте строки.'
            )
        seen[identity] = row["row"]
        row["action"] = (
            "Существующая подработа"
            if row["target_type"] == "item"
            else "Существующая позиция" if target else "Создать"
        )
        resolved.append(row)
    by_work = defaultdict(set)
    for row in resolved:
        if row["target_type"] == "item":
            item = next(c for c in children if c.pk == row["target_id"])
            by_work[(item.project_work_id, row["month"])].add(item.pk)
    for (work_id, month), item_ids in by_work.items():
        work = next(w for w in works if w.pk == work_id)
        if require_all and item_ids != {c.pk for c in work.items.all()}:
            raise ValidationError(
                f"«{work.name}»: заполните все подработы, включая нулевые объёмы."
            )
    return obj, resolved


@transaction.atomic
def apply_meeting_import(
    user, project_id, sheets, selected, start, end, target_version=None
):
    require_roles(user, PLAN_ROLES)
    project = Project.objects.select_for_update().get(
        pk=project_id, company=user.company
    )
    if target_version is not None:
        target_version = GlobalPlanVersion.objects.select_for_update().get(
            pk=target_version.pk, company=user.company, workspace__isnull=False
        )
        validate_target_period(target_version, start, end)
        if target_version.construction_object.project_id != project.pk:
            raise ValidationError("Проект не соответствует текущей версии.")
        if len(sheets) != 1 or selected != {"0"}:
            raise ValidationError("Загружается только лист объекта текущей версии.")
    chosen = [s for i, s in enumerate(sheets) if str(i) in selected]
    if not chosen or len(chosen) != len(selected):
        raise ValidationError("Выберите листы для загрузки.")
    names = [key(s["name"]) for s in chosen]
    if len(names) != len(set(names)):
        raise ValidationError("Названия выбранных объектов дублируются.")
    plans = []
    for sheet in chosen:
        if sheet["errors"]:
            raise ValidationError(f'Лист «{sheet["name"]}» содержит ошибки.')
        obj, rows = resolve_sheet(
            user.company,
            project,
            sheet,
            target_version.construction_object if target_version else None,
        )
        workspace = (
            target_version.workspace
            if target_version
            else WorkspaceService.create(
                user,
                obj,
                f"Импорт совещания — {start:%d.%m.%Y}–{end:%d.%m.%Y}",
                start,
                end,
            )
        )
        version = target_version or workspace.baseline_version
        profile = None
        totals = defaultdict(dict)
        for row in rows:
            month = date.fromisoformat(row["month"])
            if row["kind"] == "work":
                if row["target_type"] == "item":
                    item = ProjectWorkItem.objects.get(
                        pk=row["target_id"], company=user.company
                    )
                    totals[(item.project_work_id, month)][item.pk] = Decimal(
                        row["quantity"]
                    )
                    continue
                work = (
                    ProjectWork.objects.get(pk=row["target_id"], company=user.company)
                    if row["target_id"]
                    else None
                )
                if not work:
                    candidates = ProjectWork.objects.filter(
                        company=user.company, section__construction_object=obj
                    )
                    matches = [
                        v
                        for v in candidates.select_related("section")
                        if key(v.name) == key(row["name"])
                        and key(v.unit) == key(row["unit"])
                        and key(v.section.name) == key(row["section"])
                    ]
                    work = matches[0] if matches else None
                if not work:
                    section = unique_match(
                        Section.objects.filter(
                            company=user.company, construction_object=obj
                        ),
                        row["section"],
                        "Раздел",
                    )
                    if not section:
                        section = Section.objects.create(
                            company=user.company,
                            construction_object=obj,
                            name=row["section"],
                        )
                    if not profile:
                        profile = LoadProfile.objects.create(
                            company=user.company, name="Равномерный — импорт совещания"
                        )
                        LoadProfileItem.objects.create(
                            company=user.company,
                            profile=profile,
                            workday_number=1,
                            percentage=100,
                        )
                    MeasurementUnit.objects.get_or_create(
                        company=user.company,
                        symbol=row["unit"],
                        defaults={"name": row["unit"]},
                    )
                    work = ProjectWork(
                        company=user.company,
                        section=section,
                        name=row["name"],
                        unit=row["unit"],
                        load_profile=profile,
                    )
                    work.full_clean()
                    work.save()
                WorkMonthAllocation.objects.update_or_create(
                    company=user.company,
                    version=version,
                    work=work,
                    month=month,
                    defaults={
                        "quantity": Decimal(row["quantity"]),
                        "item_quantities": {},
                    },
                )
            else:
                model = Brigade if row["kind"] == "labor" else EquipmentType
                item = (
                    model.objects.get(pk=row["target_id"], company=user.company)
                    if row["target_id"]
                    else None
                )
                if not item:
                    field = "group" if row["kind"] == "labor" else "category"
                    candidates = model.objects.filter(company=user.company, merge_source__isnull=True).order_by("pk")
                    item = next((v for v in candidates if key(v.name) == key(row["name"])), None)
                if not item:
                    extra = {}
                    if row["section"]:
                        group_model = (
                            BrigadeGroup
                            if row["kind"] == "labor"
                            else EquipmentCategory
                        )
                        group = unique_match(
                            group_model.objects.filter(company=user.company),
                            row["section"],
                            "Группа ресурсов",
                        )
                        if not group:
                            group = group_model.objects.create(
                                company=user.company, name=row["section"]
                            )
                        extra["group" if row["kind"] == "labor" else "category"] = group
                    item = model.objects.create(
                        company=user.company, name=row["name"], **extra
                    )
                identity = (
                    {"brigade": item}
                    if row["kind"] == "labor"
                    else {
                        "equipment_type": item,
                        "equipment_number": row.get("equipment_number", ""),
                    }
                )
                ResourceMonthAllocation.objects.update_or_create(
                    company=user.company,
                    version=version,
                    kind=row["kind"],
                    month=month,
                    **identity,
                    defaults={"count": int(Decimal(row["quantity"]))},
                )
        for (work_id, month), values in totals.items():
            work = ProjectWork.objects.get(pk=work_id, company=user.company)
            WorkMonthAllocation.objects.update_or_create(
                company=user.company,
                version=version,
                work=work,
                month=month,
                defaults={
                    "quantity": quantity_from_totals(work_specification(work), values),
                    "item_quantities": {str(k): str(v) for k, v in values.items()},
                },
            )
        if target_version:
            target_version.snapshot = {}
            target_version.save(update_fields=["snapshot"])
        plans.append(workspace)
    return plans


def validate_target_period(version, start, end):
    from .bulk_add import editable_months

    if version.status not in ["DRAFT", "REJECTED"]:
        raise PermissionDenied(
            "Импорт доступен только для черновика или версии на доработке."
        )
    if start > end or start < version.start_date or end > version.end_date:
        raise ValidationError(
            "Период загрузки должен находиться внутри периода версии."
        )
    allowed = set(editable_months(version))
    if any(month not in allowed for month in months_between(start, end)):
        raise ValidationError(
            "В выбранном периоде есть месяцы, недоступные для редактирования."
        )


class MeetingImportForm(forms.Form):
    project = forms.ModelChoiceField(
        queryset=Project.objects.none(), label="Проект для строительных объектов"
    )
    start = forms.DateField(
        label="Начало периода загрузки",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    end = forms.DateField(
        label="Конец периода загрузки",
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
    )
    resource_rule = forms.ChoiceField(
        label="Если план людей или техники меняется по дням",
        choices=[
            ("maximum", "Взять максимум за месяц"),
            ("first", "Взять первый заполненный день"),
        ],
    )
    file = forms.FileField(label="Файл совещания (.xlsx)")
    sheet_name = forms.CharField(
        label="Лист Excel", required=False, max_length=31,
        help_text="Если лист переименован, укажите его название из Excel.",
    )

    def clean(self):
        values = super().clean()
        if values.get("start") and values.get("end"):
            if values["start"] > values["end"]:
                self.add_error("end", "Конец периода раньше начала.")
            elif len(months_between(values["start"], values["end"])) > 120:
                self.add_error("end", "Период не должен превышать десять лет.")
            if self.version:
                try:
                    validate_target_period(self.version, values["start"], values["end"])
                except ValidationError as error:
                    self.add_error("end", error)
        return values

    def __init__(self, *args, company, version=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.version = version
        self.fields["project"].queryset = Project.objects.filter(company=company)
        if version:
            del self.fields["project"]
            self.fields["sheet_name"].initial = version.construction_object.name
        else:
            del self.fields["sheet_name"]
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"



MeetingFileFormSet = forms.formset_factory(
    MeetingImportForm, extra=0, min_num=1, max_num=20,
    validate_min=True, validate_max=True, can_delete=True,
)


def batch_conflicts(company, project, batches, version):
    """Resolve again at confirmation and reject writes to the same monthly cell."""
    seen = {}
    errors = []
    for batch in batches:
        validate_target_period(version, date.fromisoformat(batch["start"]),
                               date.fromisoformat(batch["end"]))
        for sheet in batch["sheets"]:
            if sheet["errors"]:
                errors.append(f'{batch["file"]}: лист содержит ошибки.')
                continue
            try:
                _, rows = resolve_sheet(company, project, sheet, version.construction_object)
            except ValidationError as error:
                errors.extend(f'{batch["file"]}: {message}' for message in error.messages)
                continue
            for row in rows:
                identity = (row["kind"], row["target_type"], row["target_id"]) if row["target_id"] else (
                    row["kind"], key(row["section"]), key(row["name"]), key(row["unit"])
                )
                if row["kind"] in {"labor", "equipment"} and not row["target_id"]:
                    identity = (row["kind"], key(row["name"]))
                identity = (*identity, row.get("equipment_number", ""), row["month"])
                if identity in seen:
                    errors.append(
                        f'Конфликт файлов «{seen[identity]}» и «{batch["file"]}»: '
                        f'«{row["name"]}», месяц {row["month"][:7]}. '
                        'Одна позиция за месяц должна быть только в одном файле.'
                    )
                seen[identity] = batch["file"]
    return errors


@transaction.atomic
def apply_meeting_batch(user, project_id, batches, version):
    project = Project.objects.select_for_update().get(pk=project_id, company=user.company)
    version = GlobalPlanVersion.objects.select_for_update().get(pk=version.pk, company=user.company)
    errors = batch_conflicts(user.company, project, batches, version)
    if errors:
        raise ValidationError(errors)
    plans = []
    for batch in batches:
        plans.extend(apply_meeting_import(
            user, project_id, batch["sheets"], {"0"},
            date.fromisoformat(batch["start"]), date.fromisoformat(batch["end"]),
            target_version=version,
        ))
    return plans

class MeetingImportView(View):
    def target(self, request, pk):
        if pk is None:
            return None
        from .workspace_views import version_for

        version = version_for(request, pk)
        if version.status not in ["DRAFT", "REJECTED"]:
            raise PermissionDenied(
                "Импорт доступен только для черновика или версии на доработке."
            )
        return version

    def get(self, request, pk=None):
        require_roles(request.user, PLAN_ROLES)
        self.target_version = self.target(request, pk)
        from .bulk_add import editable_months

        if self.target_version:
            months = editable_months(self.target_version)
            initial_start = max(self.target_version.start_date, months[0])
            initial_end = min(
                self.target_version.end_date,
                months[-1].replace(
                    day=monthrange(months[-1].year, months[-1].month)[1]
                ),
            )
        else:
            initial_start = date.today().replace(day=1)
            initial_end = initial_start.replace(
                day=monthrange(initial_start.year, initial_start.month)[1]
            )
        return self.display(
            request,
            MeetingImportForm(
                company=request.user.company,
                version=self.target_version,
                initial={
                    "start": initial_start,
                    "end": initial_end,
                },
            ),
        )

    def display(self, request, form, **context):
        if self.target_version and form is not None and "formset" not in context:
            context["formset"] = MeetingFileFormSet(
                prefix="files", initial=[form.initial],
                form_kwargs={"company": request.user.company, "version": self.target_version},
            )
        return render(
            request,
            "planning/meeting_import.html",
            {"form": form, "target_version": self.target_version, **context},
        )

    def post(self, request, pk=None):
        require_roles(request.user, PLAN_ROLES)
        self.target_version = self.target(request, pk)
        if request.POST.get("action") == "confirm":
            try:
                payload = signing.loads(
                    request.POST.get("preview", ""), salt=SALT, max_age=3600
                )
                if (
                    payload["user"] != request.user.pk
                    or payload["company"] != request.user.company_id
                ):
                    raise PermissionDenied(
                        "Предварительный просмотр принадлежит другому пользователю."
                    )
                if payload.get("version") != (
                    self.target_version.pk if self.target_version else None
                ):
                    raise signing.BadSignature("Preview belongs to another version")
                if request.session.get("meeting_import_nonce") != payload.get("nonce"):
                    raise signing.BadSignature("Preview already used")
                selected = (
                    {"0"}
                    if self.target_version
                    else set(request.POST.getlist("sheets"))
                )
                if self.target_version and payload.get("batches"):
                    plans = apply_meeting_batch(request.user, payload["project"],
                                                payload["batches"], self.target_version)
                else:
                    plans = apply_meeting_import(
                        request.user,
                        payload["project"],
                        payload["sheets"],
                        selected,
                        date.fromisoformat(payload["start"]),
                        date.fromisoformat(payload["end"]),
                        target_version=self.target_version,
                    )
                request.session.pop("meeting_import_nonce", None)
                if self.target_version:
                    messages.success(
                        request,
                        "План загружен в текущую версию. Проверьте обновлённые месячные значения.",
                    )
                    from django.urls import reverse

                    return redirect(
                        reverse(
                            "planning:workspace_edit", args=[self.target_version.pk]
                        )
                        + "?month="
                        + payload["start"][:7]
                        + "-01"
                    )
                messages.success(
                    request,
                    f"Создано черновиков планов: {len(plans)}. Проверьте состав и сформируйте дневной план перед согласованием.",
                )
                return redirect("planning:workspace_list")
            except (signing.BadSignature, Project.DoesNotExist):
                messages.error(
                    request,
                    "Предварительный просмотр устарел или изменён. Загрузите файл заново.",
                )
                return (
                    redirect(
                        "planning:workspace_meeting_import", pk=self.target_version.pk
                    )
                    if self.target_version
                    else redirect("planning:meeting_import")
                )
            except ValidationError as error:
                return self.preview(request, payload, error.messages)
        if self.target_version and "files-TOTAL_FORMS" in request.POST:
            formset = MeetingFileFormSet(
                request.POST, request.FILES, prefix="files",
                form_kwargs={"company": request.user.company, "version": self.target_version},
            )
            if formset.is_valid():
                batches = []
                for file_form in formset:
                    if file_form.cleaned_data.get("DELETE"):
                        continue
                    values = file_form.cleaned_data
                    try:
                        sheets = parse_meeting_workbook(
                            values["file"], values["start"], values["end"],
                            values["resource_rule"], object_name=self.target_version.construction_object.name,
                            sheet_name=values.get("sheet_name"),
                        )
                        batches.append({"file": values["file"].name,
                                        "start": values["start"].isoformat(),
                                        "end": values["end"].isoformat(), "sheets": sheets})
                    except ValidationError as error:
                        file_form.add_error("file", error)
                if all(not f.errors for f in formset if not f.cleaned_data.get("DELETE")):
                    nonce = uuid4().hex
                    request.session["meeting_import_nonce"] = nonce
                    payload = {"nonce": nonce, "user": request.user.pk,
                               "company": request.user.company_id,
                               "version": self.target_version.pk,
                               "project": self.target_version.construction_object.project_id,
                               "start": min(b["start"] for b in batches),
                               "end": max(b["end"] for b in batches),
                               "sheets": [sheet for b in batches for sheet in b["sheets"]],
                               "batches": batches}
                    return self.preview(request, payload)
            return self.display(request, None, formset=formset)
        form = MeetingImportForm(
            request.POST,
            request.FILES,
            company=request.user.company,
            version=self.target_version,
        )
        if form.is_valid():
            try:
                sheets = parse_meeting_workbook(
                    form.cleaned_data["file"],
                    form.cleaned_data["start"],
                    form.cleaned_data["end"],
                    form.cleaned_data["resource_rule"],
                    sheet_name=form.cleaned_data.get("sheet_name"),
                    object_name=(
                        self.target_version.construction_object.name
                        if self.target_version
                        else None
                    ),
                )
                nonce = uuid4().hex
                request.session["meeting_import_nonce"] = nonce
                payload = {
                    "nonce": nonce,
                    "user": request.user.pk,
                    "company": request.user.company_id,
                    "version": self.target_version.pk if self.target_version else None,
                    "project": (
                        self.target_version.construction_object.project_id
                        if self.target_version
                        else form.cleaned_data["project"].pk
                    ),
                    "start": form.cleaned_data["start"].isoformat(),
                    "end": form.cleaned_data["end"].isoformat(),
                    "sheets": sheets,
                }
                return self.preview(request, payload)
            except ValidationError as error:
                form.add_error(None, error)
        return self.display(request, form)

    def preview(self, request, payload, errors=None):
        project = Project.objects.get(
            pk=payload["project"], company=request.user.company
        )
        display = []
        if payload.get("batches"):
            errors = list(errors or []) + batch_conflicts(
                request.user.company, project, payload["batches"], self.target_version)
        for index, sheet in enumerate(payload["sheets"]):
            item = {**sheet, "index": index, "errors": list(sheet["errors"])}
            try:
                obj, item["entries"] = resolve_sheet(
                    request.user.company,
                    project,
                    sheet,
                    (
                        self.target_version.construction_object
                        if self.target_version
                        else None
                    ),
                )
                item["object_action"] = "Существующий объект"
            except ValidationError as error:
                item["errors"].extend(error.messages)
            if payload.get("batches"):
                batch = payload["batches"][index]
                item.update(file=batch["file"], start=date.fromisoformat(batch["start"]),
                            end=date.fromisoformat(batch["end"]))
            display.append(item)
        return self.display(
            request,
            None,
            sheets=display,
            project=project,
            start=date.fromisoformat(payload["start"]),
            end=date.fromisoformat(payload["end"]),
            errors=errors,
            blocked=bool(errors) or any(s["errors"] for s in display),
            preview=signing.dumps(payload, salt=SALT, compress=True),
        )
