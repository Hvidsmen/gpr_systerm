"""Small, version-checked writes from cells of the transport roster."""
import json
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms.models import model_to_dict
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST,require_GET
from core.fact_edit_window import require_fact_day
from core.fact_history import with_history_actor
from core.permissions import require_roles
from apps.resources.models import Equipment,Employee
from . import transport
from .transport_details_models import EquipmentDayDetails,EquipmentServiceStatus,DETAIL_FIELDS
from .transport_detail_forms import DetailsForm,EMPLOYEE_FIELDS,STATUS_FIELDS
from .transport_detail_values import STATUS_SEEDS,STATUS_KINDS


def ensure_statuses(company):
    # Companies can be created after the schema migration.
    if EquipmentServiceStatus.objects.filter(company=company).exists():return
    for kind,names in STATUS_SEEDS.items():
        for name in names:EquipmentServiceStatus.objects.get_or_create(company=company,kind=kind,name=name)


@login_required
@require_GET
def choices(request):
    require_roles(request.user,transport.OPERATORS)
    from apps.resources.group_order import ordered
    employees=ordered(Employee.objects.filter(company=request.user.company,is_active=True).select_related('position__planning_mapping__brigade__group'),group_path='position__planning_mapping__brigade__group',within=('position__name','last_name','first_name','pk'))
    return JsonResponse({'options':[{'id':e.pk,'name':e.display_name+(' · № '+e.employee_number if e.employee_number else '')+(' · '+str(e.position) if e.position_id else '')} for e in employees]})


@login_required
@require_POST
def add_status(request):
    require_roles(request.user,transport.OPERATORS)
    try:
        data=json.loads(request.body);kind=data.get('kind');name=' '.join(str(data.get('name','')).split())
        if kind not in dict(STATUS_KINDS) or not name or len(name)>150:raise ValidationError('Выберите справочник и укажите название до 150 символов.')
        with transaction.atomic():
            transport._company_mutex(request.user)
            from apps.accounts.models import Company
            Company.objects.select_for_update().get(pk=request.user.company_id)
            existing=next((row for row in EquipmentServiceStatus.objects.filter(company=request.user.company,kind=kind) if row.name.casefold()==name.casefold()),None)
            if existing and not existing.is_active:raise ValidationError('Такое значение отключено. Обратитесь к администратору справочника.')
            row=existing or EquipmentServiceStatus.objects.create(company=request.user.company,kind=kind,name=name)
            if not existing:transport.audit(request.user,row,'CREATE',{'kind':kind,'name':name})
        return JsonResponse({'id':row.pk,'name':row.name,'kind':row.kind})
    except (json.JSONDecodeError,ValidationError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Неверные данные.'},status=400)


@with_history_actor
@transaction.atomic
def save_cell(user,camp,day,machine,field,value,signed,session):
    transport.authorize(user,camp);require_fact_day(user,day)
    versions=transport._payload(user,camp,day,signed)
    machines,controls=transport._lock(user,[machine.pk]);machine=machines[0]
    transport._check(user,machine,controls[machine.pk],versions,transport._session(session))
    if not transport.present(user.company,day).filter(equipment=machine,camp=camp).exists():raise ValidationError('Размещение машины изменилось. Обновите страницу.')
    if field=='condition':
        transport.set_condition(user,camp,day,[machine.pk],str(value),signed=signed,session=session)
    elif field=='status':
        current=machine.transport_days.filter(date=day).first()
        target={'construction_object':current.construction_object,'work':current.work,'work_item':current.work_item} if current and value=='WORKED' else {}
        transport.save_day(user,camp,day,[machine.pk],str(value),signed=signed,session=session,**target)
    elif field in {'target','day_comment'}:
        current=machine.transport_days.filter(date=day).first()
        target={'construction_object':current.construction_object,'work':current.work,'work_item':current.work_item} if current and current.status=='WORKED' else {}
        if field=='target':
            from .transport_views import target_choices
            _,mapping=target_choices(user,camp,day,current=[current] if current else [])
            if value not in mapping:raise ValidationError('Работа недоступна для выбранного ВЖГ.')
            obj,work,item=mapping[value];target={'construction_object':obj,'work':work,'work_item':item}
        transport.save_day(user,camp,day,[machine.pk],current.status if current else 'WORKED',signed=signed,session=session,comment=str(value) if field=='day_comment' else current.comment if current else '',**target)
    elif field in dict(DETAIL_FIELDS):
        current=EquipmentDayDetails.objects.filter(company=user.company,equipment=machine,date=day).first()
        previous=EquipmentDayDetails.objects.filter(company=user.company,equipment=machine,date__lt=day).first()
        source=current or previous
        values=model_to_dict(source,fields=[name for name,_ in DETAIL_FIELDS]) if source else {}
        if source and source.date<day:
            for name in EMPLOYEE_FIELDS:values[name]=None
        if field in EMPLOYEE_FIELDS and value and not machine.transport_days.filter(company=user.company,date=day,status='WORKED').exists():raise ValidationError('Сначала отметьте, что техника работала за выбранный день.')
        values[field]=value
        if field=='in_gpr':values[field]={'true':True,'false':False,'':None,'unknown':None}.get(str(value).lower(),value)
        form=DetailsForm(values,instance=current,company=user.company)
        if not form.is_valid():raise ValidationError(['; '.join(errors) for errors in form.errors.values()])
        row=form.save(commit=False);row.company=user.company;row.equipment=machine;row.camp=camp;row.date=day
        if not row.pk:row.recorded_by=user;row.source_details={name:value for name,value in previous.source_details.items() if name not in {'shift_one','shift_two'}} if previous else {}
        row.full_clean();row.save();transport._bump(controls[machine.pk])
    else:raise ValidationError('Это поле нельзя изменить в таблице.')
    require_fact_day(user,day)
    return transport.snapshot(user,camp,day)


@login_required
@require_POST
def update(request):
    require_roles(request.user,transport.OPERATORS)
    try:
        from datetime import date
        data=json.loads(request.body)
        camp=get_object_or_404(transport.camps_for(request.user),pk=data.get('camp'))
        machine=get_object_or_404(Equipment,company=request.user.company,pk=data.get('equipment'))
        signed=save_cell(request.user,camp,date.fromisoformat(data.get('date','')),machine,data.get('field'),data.get('value'),data.get('signed'),data.get('session'))
        record=machine.transport_days.filter(company=request.user.company,date=date.fromisoformat(data['date'])).first()
        return JsonResponse({'saved':True,'signed':signed,'condition':transport.condition_on(machine,date.fromisoformat(data['date'])),
            'status':record.status if record else '', 'target':f'work:{record.work_id}:{record.work_item_id or 0}' if record and record.work_id else f'object:{record.construction_object_id}' if record and record.construction_object_id else ''})
    except (ValidationError,ValueError,TypeError) as exc:return JsonResponse({'error':'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Неверные данные.'},status=409)


def prepare_rows(user,camp,day,rows,editable):
    from .transport_detail_values import SOURCE_DETAIL_FIELDS
    ensure_statuses(user.company)
    ids=[row['machine'].pk for row in rows];details={}
    for detail in EquipmentDayDetails.objects.filter(company=user.company,equipment_id__in=ids,date__lte=day).select_related('shift_one_employee__position','shift_two_employee__position','request_stage','parts_stage','maintenance_stage').order_by('equipment_id','-date','-pk'):
        details.setdefault(detail.equipment_id,detail)
    source_names={'shift_one_employee':'shift_one','shift_two_employee':'shift_two','repair_started_on':'repair_date','parts_expected_on':'expected_parts_date','request_stage':'request_status','parts_stage':'parts_status','maintenance_stage':'maintenance','repair_stage':'repair_status'}
    for row in rows:
        detail=details.get(row['machine'].pk)
        initial={name:None for name in EMPLOYEE_FIELDS} if detail and detail.date<day else {}
        form=DetailsForm(instance=detail,initial=initial,company=user.company,lazy=True,prefix='machine-'+str(row['machine'].pk))
        for name,field in form.fields.items():
            field.disabled=not editable or name in EMPLOYEE_FIELDS and row['status']!='WORKED';field.widget.attrs['aria-label']=str(field.label)+' · '+str(row['machine'])
        row['details_form']=form
        for bound in form.visible_fields():bound.source_value=(detail.source_details.get(source_names.get(bound.name,''),'') if detail and not (bound.name in EMPLOYEE_FIELDS and detail.date<day) else '')
        row['details']=detail
