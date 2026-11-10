"""Import the maintained Google Sheets export into the shared talent pool."""
from datetime import date,datetime,time
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import TalentPoolCandidate

SHEET='Магазин 2026'
HEADERS=['№ п.п.','ФИО','Должность','Строительный участок','Дата рождения','Серия номер паспорта','Номер телефона',
    'Согласован с непосредственным руководителем','Согласование СБ','СКЗИ (действует до)','Медицинский осмотр',
    'Документы направлены на пропуск','Базовый город','Готовность к заезду','Дата прибытия в Дудинку',
    'Ответственный HR','Название агентства','Примечания','ФИО (дублирование без пробелов)']
KEYS=['number','full_name','position','construction_site','birth_date','passport','phone','manager_approval','security_approval',
    'skzi_until','medical_exam','pass_documents','base_city','readiness','dudinka_arrival','responsible_hr','agency','notes','duplicate_name']

def _text(value):
    if value is None:return ''
    if isinstance(value,datetime):return value.date().isoformat()
    if isinstance(value,date):return value.isoformat()
    if isinstance(value,time):return value.strftime('%H:%M')
    if isinstance(value,float) and value.is_integer():return str(int(value))
    return str(value).strip()

def _name(value):return ' '.join(_text(value).split())

@transaction.atomic
def import_sheet(user,file):
    try:
        from openpyxl import load_workbook
        workbook=load_workbook(file,read_only=True,data_only=True)
    except Exception as exc:raise ValidationError('Не удалось прочитать XLSX-файл.') from exc
    if SHEET not in workbook.sheetnames:raise ValidationError(f'В файле нет листа «{SHEET}».')
    sheet=workbook[SHEET];iterator=sheet.iter_rows(values_only=True)
    try:headers=[_text(value) for value in next(iterator)[:len(HEADERS)]]
    except StopIteration:raise ValidationError('Лист пуст.')
    if headers[:len(HEADERS)]!=HEADERS:raise ValidationError('Структура листа «Магазин 2026» не совпадает с ожидаемой. Проверьте заголовки.')
    existing={_name(row.full_name).casefold():row for row in TalentPoolCandidate.objects.select_for_update().filter(company=user.company)}
    seen=set();created=updated=skipped=0
    for values in iterator:
        data={key:_text(value) for key,value in zip(KEYS,values[:len(KEYS)])};full_name=_name(data['full_name']);key=full_name.casefold()
        if not full_name or full_name in {'#REF!','#N/A'}:skipped+=1;continue
        if key in seen:raise ValidationError(f'В листе повторяется ФИО: {full_name}.')
        seen.add(key);data['full_name']=full_name
        defaults={'phone':data['phone'],'notes':data['notes'],'source_data':data,'is_active':True}
        row=existing.get(key)
        if row:
            changed=False
            for field,value in defaults.items():
                if getattr(row,field)!=value:setattr(row,field,value);changed=True
            if changed:row.save(update_fields=[*defaults,'updated_at']);updated+=1
            else:skipped+=1
        else:
            TalentPoolCandidate.objects.create(company=user.company,full_name=full_name,created_by=user,**defaults);created+=1
    return {'created':created,'updated':updated,'skipped':skipped}
