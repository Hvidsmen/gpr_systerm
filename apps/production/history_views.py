"""Read-only, company- and object-scoped actual-data history."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import render
from django.views.decorators.http import require_GET
from core.permissions import READ_ROLES, FACT_ROLES, require_roles, role_code, scope_queryset
from apps.projects.models import ConstructionObject
from .history_models import FactRevision

TITLES={
 'production.workopeningbalance':'Входящий остаток работы',
 'production.dailyfact':'Работа', 'production.employeeactivity':'Сотрудник за смену',
 'production.employeeworklink':'Связь сотрудника и работы', 'production.equipmentactivity':'Техника на объекте',
 'production.equipmentdayrecord':'Техника на ВЖГ за день','production.equipmentcampstay':'Пребывание машины в ВЖГ',
 'production.equipmenttransportevent':'Событие транспорта','production.equipmentassignment':'Назначение техники',
 'production.equipmentstatechange':'Состояние техники','production.laborfact':'Итог по должности',
 'production.equipmentfact':'Итог по технике','resources.transportimportrow':'Строка импорта техники','production.equipmentdaydetails':'Сведения по технике','production.campfuelday':'Баланс ГСМ на ВЖГ','production.fuelfact':'ГСМ','production.dayactuation':'Актировка',
 'personnel.campstay':'Пребывание сотрудника в ВЖГ','personnel.housingevent':'Событие персонала',
}
TITLES.update({'personnel.lodgingstay':'Проживание','personnel.lodgingevent':'Событие коменданта','personnel.lodgingbuilding':'Вагон / корпус','personnel.lodgingbed':'Жилое место','personnel.lodgingresolution':'Объяснение сверки'})
DIRECTIONS=[('lodging','Комендант'),('works','Производство'),('labor','Персонал'),('mechanization','Транспорт и ГСМ (прежний учёт)'),('fuel','ГСМ')]
FIELD_LABELS={
 'quantity':'Входящий выполненный объём',
 'actual_quantity':'Фактический объём','actual_value':'Стоимость','reported_by_id':'Первоначальный автор',
 'recorded_by_id':'Первоначальный автор','actual_workers':'Численность','actual_count':'Количество техники',
 'planned_count':'Плановое количество','actual_hours':'Человеко-часы','actual_machine_hours':'Машино-часы',
 'actual_liters':'Расход, л','actual_balance':'Остаток, л','opening_balance':'Начальный остаток, л','received_liters':'Поступление, л',
 'comment':'Примечание','activity':'Занятость / пояснение','date':'Дата','shift':'Смена','general':'Без привязки к работе',
 'work_id':'Работа','project_work_id':'Работа','work_item_id':'Работа в комплексе','employee_id':'Сотрудник',
 'equipment_id':'Машина','position_id':'Должность','brigade_id':'Должность','equipment_type_id':'Техника для планирования',
 'construction_object_id':'Объект','camp_id':'ВЖГ','from_camp_id':'Из ВЖГ','to_camp_id':'В ВЖГ',
 'state':'Состояние','condition':'Техническое состояние','status':'Отметка за день','kind':'Событие',
 'arrived_on':'Приезд / заезд','departed_on':'Выезд','effective_date':'Состояние действует с','start_date':'Начало','end_date':'Окончание',
 'cancelled_at':'Дата отмены','cancelled_by_id':'Кто отменил','cancellation_reason':'Причина отмены','cancel_reason':'Причина отмены',
 'assignment_reviewed':'Связи проверены','deviation_reason_id':'Причина отклонения','outside_smg':'Вне СМГ','smg_plan_quantity':'План СМГ',
 'reason':'Причина / пояснение','stay_id':'Период пребывания','activity_id':'Сотрудник за смену','is_cancelled':'Отменено','created_by_id':'Первоначальный автор',
 'planned_workers':'Плановая численность','planned_hours':'Плановые часы','planned_machine_hours':'Плановые машино-часы','hourly_rate':'Ставка за час',
 'entry_direction':'Направление ввода','planning_position_id':'Учитывается как должность СМГ','departure_destination':'Куда выезжает','source_file':'Файл','source_sheet':'Лист','source_row':'Строка','original':'Исходные данные','data':'Исправленные данные','error':'Ошибка','duplicate_of_id':'Совпавшая машина','resolved_by_id':'Кто обработал','resolved_at':'Дата обработки',
 'fuel_type':'Вид ГСМ','equipment_number':'Номер машины','equipment_ref':'Техника / назначение ГСМ',
}
from .transport_details_models import DETAIL_FIELDS
FIELD_LABELS.update(dict(DETAIL_FIELDS))
FIELD_LABELS.update({'organization_snapshot':'Организация при заселении','position_snapshot':'Должность при заселении','department_snapshot':'Подразделение при заселении','bed_id':'Место','from_bed_id':'С прежнего места','to_bed_id':'На новое место','room':'Комната','number':'Номер места','building_id':'Вагон / корпус','planned_departure':'Плановое освобождение','keys_issued':'Ключи выданы','pass_number':'Пропуск','fingerprint':'Версия сверки'})
CHOICES={'works':'Производство','labor':'Персонал','WORKED':'Работала','WORKING':'Работала','IDLE':'Простой','READY':'Исправна','REPAIR':'В ремонте','SCRAP':'Под списание / на запчасти',
 'CHECKIN':'Заселение','CHECKOUT':'Выселение','MOVE':'Переселение','UPDATE':'Изменение сведений','AVAILABLE':'Доступно','RESERVED':'Зарезервировано','UNAVAILABLE':'Недоступно','ARRIVAL':'Приезд / заезд','DEPARTURE':'Выезд','TRANSFER':'Перевод','WRITEOFF':'Списание','CONDITION':'Состояние','CANCEL':'Отмена'}


def visible_revisions(user):
    records=FactRevision.objects.filter(company=user.company)
    role=role_code(user)
    direction={'DISPATCHER':'works','PERSONNEL_DISPATCHER':'labor','MECHANIZATION_DISPATCHER':'mechanization'}.get(role)
    if role=='FUEL_WAREHOUSE':
        records=records.filter(entity_type__in=['production.campfuelday','production.fuelfact']);direction=None
    if role=='COMMANDANT':direction='lodging'
    if role=='PERSONNEL_DISPATCHER':records=records.filter(direction__in=['labor','lodging']);direction=None
    if direction=='mechanization':
        records=records.filter(Q(direction=direction)|Q(entity_type__in=['production.campfuelday','production.fuelfact']));direction=None
    if direction:records=records.filter(direction=direction)
    if role in {'DISPATCHER','PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER','ALL_FACT_DISPATCHER','PLANNER','FOREMAN','FUEL_WAREHOUSE'}:
        objects=scope_queryset(ConstructionObject.objects.all(),user).values('pk')
        from .transport import camps_for
        camps=camps_for(user).values('pk')
        records=records.filter(Q(object_id__in=objects)|Q(camp_ids__0__in=camps)|Q(camp_ids__1__in=camps))
    return records


class HistoryFilter(forms.Form):
    direction=forms.ChoiceField(label='Направление',required=False,choices=[('','Все доступные'),*DIRECTIONS])
    date=forms.DateField(label='Дата факта',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    editor=forms.CharField(label='Кто изменил',required=False,max_length=150)
    entity=forms.ChoiceField(label='Тип записи',required=False,choices=[('','Все записи'),*TITLES.items()])
    id=forms.IntegerField(label='Номер записи',required=False,min_value=1)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():field.widget.attrs['class']='form-select' if isinstance(field.widget,forms.Select) else 'form-control'


def display(value, field, snap):
    if value is None or value=='':return '—'
    reference=(snap or {}).get('references',{}).get(field)
    if reference:return reference
    if isinstance(value,bool):return 'Да' if value else 'Нет'
    return CHOICES.get(value,str(value)) if isinstance(value,str) else str(value)


def changes(record):
    before=record.before or {'fields':{}};after=record.after or {'fields':{}}
    rows=[]
    for field in sorted(before['fields'].keys()|after['fields'].keys()):
        if field in {'id','company_id','created_at','updated_at','previous_stay_id','resulting_stay_id','previous_active','revision','standing_assignment_id','source_state_id','assignment_id'}:continue
        if field=='activity_id' and record.entity_type=='production.equipmentdayrecord':continue
        old=before['fields'].get(field);new=after['fields'].get(field)
        if old in (None,'') and new in (None,''):continue
        if record.operation=='UPDATE' and old==new:continue
        rows.append({'label':FIELD_LABELS.get(field,field),'before':display(old,field,before),'after':display(new,field,after)})
    return rows


@login_required
@require_GET
def history(request):
    require_roles(request.user,READ_ROLES|FACT_ROLES|{'COMMANDANT'})
    form=HistoryFilter(request.GET)
    records=visible_revisions(request.user)
    if form.is_valid():
        data=form.cleaned_data
        if data['direction']:records=records.filter(direction=data['direction'])
        if data['date']:records=records.filter(fact_date=data['date'])
        if data['editor']:records=records.filter(actor_name__icontains=data['editor'])
        if data['entity']:records=records.filter(entity_type=data['entity'])
        if data['id']:records=records.filter(entity_id=str(data['id']))
    else:records=records.none()
    page=Paginator(records,40).get_page(request.GET.get('page'))
    for record in page.object_list:
        record.type_label=TITLES.get(record.entity_type,record.entity_type)
        record.changes=changes(record)
    query=request.GET.copy();query.pop('page',None)
    return render(request,'production/fact_history.html',{'filter_form':form,'page_obj':page,'filter_query':query.urlencode()},status=200 if form.is_valid() else 400)
