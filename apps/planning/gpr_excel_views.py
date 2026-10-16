from calendar import monthrange
from datetime import date
from uuid import uuid4
from collections import Counter
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.utils import timezone
from core.permissions import PLAN_ROLES, require_roles
from apps.projects.models import ConstructionObject
from apps.production.forms import CompanyFormMixin
from apps.resources.models import Brigade, BrigadeGroup, EquipmentType, EquipmentCategory
from .models import ResourceMonthAllocation, GprImportReceipt
from .workspace_services import WorkspaceService
from .meeting_import import resolve_sheet, apply_meeting_import, unique_match, validate_target_period
from .gpr_excel import parse_gpr, number, norm

SALT = 'planning.contractor-gpr.v1'


class GprUploadForm(CompanyFormMixin, forms.Form):
    construction_object = forms.ModelChoiceField(queryset=ConstructionObject.objects.all(), label='Строительный объект')
    file = forms.FileField(label='Файл ГПР (.xls / .xlsx)', widget=forms.ClearableFileInput(attrs={'accept':'.xls,.xlsx'}))
    sheet = forms.CharField(required=False, label='Лист Excel', help_text='Можно указать другой лист. Автоматически выбирается «ГПР» или первый лист с месячными столбцами.')
    year = forms.IntegerField(required=False, min_value=1900, max_value=2100, label='Заменить годы Excel (необязательно)', help_text='По умолчанию годы берутся из файла. Заполняйте только для намеренного переноса плана. Год января в плане. Например, 2027: ноябрь и декабрь перед январём относятся к 2026. Пустое поле сохраняет годы Excel.')
    title = forms.CharField(required=False, max_length=255, label='Название нового ГПР')


class MonthMapForm(forms.Form):
    def __init__(self, *args, payload, **kwargs):
        super().__init__(*args, **kwargs)
        for item in payload['months']:
            self.fields[f"month_{item['column']}"] = forms.DateField(label=f"Столбец {item['excel_column']} · Excel {item['original']}", initial=item['month'], widget=forms.DateInput(format='%Y-%m-%d', attrs={'type':'date', 'class':'form-control'}))
    def clean(self):
        data = super().clean()
        values = list(data.values())
        if any(value.day != 1 for value in values):
            raise ValidationError('Укажите первое число каждого месяца.')
        if len(values)!=len(set(values)):
            raise ValidationError('Столбцы не должны относиться к одному месяцу.')
        if values and ((max(values).year-min(values).year)*12+max(values).month-min(values).month)>120:
            raise ValidationError('Период ГПР не должен превышать десять лет.')
        return data


def resource_match(company, row, candidates=None):
    model = Brigade if row['kind']=='labor' else EquipmentType
    field = 'group' if row['kind']=='labor' else 'category'
    if candidates is None:
        candidates = list(model.objects.filter(company=company, merge_source__isnull=True).select_related(field).order_by('pk'))
    matches = [item for item in candidates if norm(item.name)==norm(row['name'])]
    scoped = [item for item in matches if getattr(item,field) and norm(getattr(item,field).name)==norm(row['section'])]
    choices = scoped if row["section"] else matches
    return choices[0] if choices else None


def resolved_preview(user, obj, payload):
    month_by_column = {row['column']: row['month'] for row in payload['entries']}
    # Columns remain distinct while resolving catalogs; their dates are editable.
    entries = [{**row, 'month': date(1900+row['column']//12,row['column']%12+1,1).isoformat()} for row in payload['entries'] if row['kind']=='work']
    sheet = {'name': obj.name, 'entries': entries, 'warnings': [], 'errors': []}
    _, works = resolve_sheet(user.company, obj.project, sheet, target_object=obj, allow_parent_quantity=True)
    works = [{**row, 'month': month_by_column[row['column']]} for row in works]
    resources = []
    catalogs = {'labor': list(Brigade.objects.filter(company=user.company, merge_source__isnull=True).select_related('group').order_by('pk')), 'equipment': list(EquipmentType.objects.filter(company=user.company, merge_source__isnull=True).select_related('category').order_by('pk'))}
    warned = set()
    for row in payload['entries']:
        if row['kind']=='work': continue
        if row['kind']=='fuel':
            name = norm(row['name'])
            fuel_type = 'DIESEL' if 'дизел' in name or name=='дт' else 'PETROL_92' if '92' in name else 'PETROL_95' if '95' in name else 'PETROL_98' if '98' in name else 'GAS' if 'газ' in name else None
            if fuel_type is None:
                raise ValidationError(f"Укажите поддерживаемый вид ГСМ для «{row['name']}».")
            resources.append({**row, 'action': 'Расход ГСМ', 'fuel_type': fuel_type})
        else:
            target = resource_match(user.company, row, catalogs[row['kind']])
            matches = [item for item in catalogs[row['kind']] if norm(item.name)==norm(row['name'])]
            if target is not None and len(matches)>1 and row['name'] not in warned:
                warned.add(row['name'])
                sheet['warnings'].append(f"«{row['name']}»: найдено несколько записей справочника; выбран ID {target.pk} с учётом группы. Проверьте позицию перед сохранением.")
            resources.append({**row, 'target_id': target.pk if target else None, 'action': f'Существующая позиция · ID {target.pk}' if target else 'Создать'})
    labels = {'work':'Работы','labor':'Люди','equipment':'Техника','fuel':'ГСМ'}
    return [{**row, 'kind_label': labels[row['kind']]} for row in works+resources], sheet['warnings']


@transaction.atomic
def apply_gpr(user, obj, payload, dates, nonce, target_version=None):
    require_roles(user, PLAN_ROLES)
    obj = ConstructionObject.objects.select_for_update().get(pk=obj.pk, company=user.company)
    receipt = GprImportReceipt.objects.filter(nonce=nonce, company=user.company, imported_by=user).first()
    if receipt:
        if receipt.workspace_id is None:
            raise ValidationError('Созданный по этому импорту ГПР удалён. Прочитайте файл заново.')
        return receipt.workspace
    rows = [{**row, 'month': dates[f"month_{row['column']}"].isoformat()} for row in payload['entries']]
    mapped = {**payload, 'entries': rows}
    resolved, _ = resolved_preview(user, obj, mapped)
    if not resolved:
        raise ValidationError('Нет данных для импорта.')
    start, last = min(dates.values()), max(dates.values())
    end = last.replace(day=monthrange(last.year,last.month)[1])
    if target_version is not None:
        from .models import GlobalPlanVersion
        target_version = GlobalPlanVersion.objects.select_for_update().defer('snapshot').get(pk=target_version.pk, company=user.company, workspace__isnull=False)
        if target_version.construction_object_id != obj.pk:
            raise ValidationError('Объект версии не совпадает с объектом импорта.')
        validate_target_period(target_version, start, end)
        workspace = target_version.workspace
    else:
        workspace = WorkspaceService.create(user,obj,payload.get('title') or f'ГПР {obj.name} · импорт Excel',start,end)
    version = target_version or workspace.baseline_version
    works = [row for row in rows if row['kind']=='work']
    if works:
        apply_meeting_import(user,obj.project_id,[{'name':obj.name,'entries':works,'warnings':[],'errors':[]}],{'0'},start,end,target_version=version, allow_parent_quantity=True)
    cached = {}
    for row in resolved:
        if row['kind']=='work': continue
        month = date.fromisoformat(row['month'])
        if row['kind']=='fuel':
            ResourceMonthAllocation.objects.update_or_create(company=user.company,version=version,month=month,kind='fuel',fuel_type=row['fuel_type'],defaults={'liters':number(row['quantity'])})
            continue
        model = Brigade if row['kind']=='labor' else EquipmentType
        cache_key = row['kind'],norm(row['name']),norm(row['section'])
        target = cached.get(cache_key)
        if target is None:
            target = model.objects.get(pk=row['target_id'],company=user.company) if row['target_id'] else resource_match(user.company,row)
            if target is None:
                group_model = BrigadeGroup if row['kind']=='labor' else EquipmentCategory
                group = unique_match(group_model.objects.filter(company=user.company),row['section'],'Группа ресурсов')
                if group is None: group = group_model.objects.create(company=user.company,name=row['section'])
                target = model.objects.create(company=user.company,name=row['name'],**{'group' if row['kind']=='labor' else 'category':group})
            cached[cache_key] = target
        identity = {'brigade':target} if row['kind']=='labor' else {'equipment_type':target,'equipment_number':row.get('equipment_number','')}
        allocation = ResourceMonthAllocation(company=user.company,version=version,month=month,kind=row['kind'],count=int(number(row['quantity'],count=True)),**identity)
        existing = ResourceMonthAllocation.objects.filter(company=user.company,version=version,month=month,kind=row['kind'],**identity).first()
        if existing:
            existing.count = allocation.count
            allocation = existing
        allocation.full_clean()
        allocation.save()
    if target_version is not None:
        version.snapshot = {}
        version.save(update_fields=['snapshot'])
    GprImportReceipt.objects.create(company=user.company,nonce=nonce,workspace=workspace,imported_by=user)
    return workspace


class GprExcelImport(View):
    def dispatch(self, request, *args, **kwargs):
        require_roles(request.user, PLAN_ROLES)
        from .meeting_import import MeetingImportView
        self.target_version = MeetingImportView().target(request, kwargs.get('pk'))
        return super().dispatch(request, *args, **kwargs)

    def upload_form(self, request, *args, **kwargs):
        form = GprUploadForm(*args, user=request.user, **kwargs)
        if self.target_version:
            form.fields['construction_object'].initial = self.target_version.construction_object
            form.fields['construction_object'].disabled = True
            form.fields.pop('title')
        return form

    def get(self, request, pk=None):
        require_roles(request.user,PLAN_ROLES)
        return render(request,'planning/gpr_excel_import.html',{'form':self.upload_form(request),'target_version':self.target_version})

    def post(self, request, pk=None):
        require_roles(request.user,PLAN_ROLES)
        token = request.POST.get('preview_token')
        if token:
            try:
                payload = signing.loads(token,salt=SALT,max_age=3600)
                if payload['user']!=request.user.pk or payload['company']!=request.user.company_id or payload.get('target_version') != (self.target_version.pk if self.target_version else None):
                    raise signing.BadSignature()
                obj = get_object_or_404(ConstructionObject,pk=payload['object'],company=request.user.company)
                form = MonthMapForm(request.POST,payload=payload)
                if form.is_valid():
                    workspace = apply_gpr(request.user,obj,payload,form.cleaned_data,payload['nonce'],target_version=self.target_version)
                    return redirect('planning:workspace_edit',pk=self.target_version.pk) if self.target_version else redirect('planning:workspace_detail',pk=workspace.pk)
                rows, warnings = resolved_preview(request.user,obj,payload)
                return self.preview(request,obj,payload,token,form,rows,warnings)
            except (signing.BadSignature,ValidationError) as exc:
                form = self.upload_form(request,{})
                form.is_valid()
                form.add_error(None,exc if isinstance(exc,ValidationError) else 'Предварительный просмотр устарел. Прочитайте файл заново.')
                return render(request,'planning/gpr_excel_import.html',{'form':form,'target_version':self.target_version},status=400)
        form = self.upload_form(request,request.POST,request.FILES)
        if form.is_valid():
            try:
                data=form.cleaned_data
                uploaded=data['file']
                if uploaded.size>8*1024*1024:raise ValidationError('Размер файла превышает 8 МБ.')
                payload=parse_gpr(uploaded.read(),uploaded.name,data['sheet'],data['year'])
                obj=data['construction_object']
                payload.update({'title':data.get('title',''),'target_version':self.target_version.pk if self.target_version else None,'user':request.user.pk,'company':request.user.company_id,'object':obj.pk,'nonce':str(uuid4())})
                rows,warnings=resolved_preview(request.user,obj,payload)
                token=signing.dumps(payload,salt=SALT,compress=True)
                return self.preview(request,obj,payload,token,MonthMapForm(payload=payload),rows,warnings)
            except ValidationError as exc:
                form.add_error(None,exc)
        return render(request,'planning/gpr_excel_import.html',{'form':form,'target_version':self.target_version},status=400)

    def preview(self, request,obj,payload,token,month_form,rows,warnings):
        return render(request,'planning/gpr_excel_preview.html',{'target_version':self.target_version,'object':obj,'payload':payload,'token':token,'month_form':month_form,'rows':rows[:300],'row_count':len(rows),'warnings':payload['warnings']+warnings,'counts':dict(Counter(row['kind'] for row in rows))})
