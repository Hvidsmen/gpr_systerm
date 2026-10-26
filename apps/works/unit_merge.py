"""Merge synonymous measurement units without converting stored quantities."""
import hashlib,json
from django import forms
from django.contrib import messages
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render,redirect
from django.views import View
from core.permissions import PLAN_ROLES,require_roles
from apps.accounts.models import Company
from apps.resources.models import Brigade,EquipmentType,FuelType
from .models import MeasurementUnit,MeasurementUnitAlias,ProjectWork,ProjectWorkItem,WorkTemplate,WorkTemplateItem

SALT='measurement-unit-merge-v1'
MODELS=[ProjectWork,ProjectWorkItem,WorkTemplate,WorkTemplateItem,Brigade,EquipmentType,FuelType]


class MergeForm(forms.Form):
    units=forms.ModelMultipleChoiceField(label='Объединяемые единицы',queryset=MeasurementUnit.objects.none())
    target=forms.ModelChoiceField(label='Оставить основную единицу',queryset=MeasurementUnit.objects.none())
    equivalent=forms.BooleanField(label='Это равнозначные обозначения одной единицы, коэффициент пересчёта 1:1')

    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs)
        for name in ['units','target']:
            self.fields[name].queryset=MeasurementUnit.objects.filter(company=company)
            self.fields[name].widget.attrs['class']='form-select'

    def clean(self):
        data=super().clean()
        if data.get('units') is not None and data.get('target') and not data['units'].exclude(pk=data['target'].pk).exists():
            raise forms.ValidationError('Выберите хотя бы одну другую единицу для объединения с основной.')
        if data.get('units') is not None and data.get('target'):
            symbols=list(data['units'].exclude(pk=data['target'].pk).values_list('symbol',flat=True))
            for model in MODELS:
                if len(data['target'].symbol)>model._meta.get_field('unit').max_length and model.objects.filter(company=data['target'].company,unit__in=symbols).exists():
                    raise forms.ValidationError('Обозначение основной единицы слишком длинное для связанных записей: '+str(model._meta.verbose_name_plural))
        return data


def inspect(company,units,target):
    sources=[unit for unit in units if unit.pk!=target.pk];symbols=[unit.symbol for unit in sources]
    summary=[];state={'units':[(u.pk,u.symbol,u.name,str(u.updated_at)) for u in sorted([*sources,target],key=lambda u:u.pk)],'refs':{}}
    for model in MODELS:
        rows=list(model.objects.filter(company=company,unit__in=symbols).order_by('pk').values_list('pk','unit'))
        summary.append({'label':str(model._meta.verbose_name_plural),'count':len(rows)})
        state['refs'][model._meta.label]=rows
    state['aliases']=list(MeasurementUnitAlias.objects.filter(company=company).order_by('pk').values_list('symbol','target_id'))
    digest=hashlib.sha256(json.dumps(state,sort_keys=True,default=str).encode()).hexdigest()
    return sources,summary,digest


@transaction.atomic
def merge(user,token):
    require_roles(user,PLAN_ROLES)
    try:data=signing.loads(token,salt=SALT,max_age=1800)
    except signing.BadSignature:raise ValidationError('Предпросмотр устарел. Выберите единицы и проверьте объединение снова.')
    if data.get('user')!=user.pk or data.get('company')!=user.company_id:raise ValidationError('Предпросмотр относится к другому пользователю или компании.')
    Company.objects.select_for_update().get(pk=user.company_id)
    ids=set(data['sources'])|{data['target']}
    units=list(MeasurementUnit.objects.select_for_update().filter(company=user.company,pk__in=ids).order_by('pk'))
    if len(units)!=len(ids):raise ValidationError('Список единиц изменён. Повторите предпросмотр.')
    target=next(unit for unit in units if unit.pk==data['target'])
    symbols=[unit.symbol for unit in units if unit.pk!=target.pk]
    for model in MODELS:list(model.objects.select_for_update().filter(company=user.company,unit__in=symbols).order_by('pk').values_list('pk',flat=True))
    list(MeasurementUnitAlias.objects.select_for_update().filter(company=user.company).values_list('pk',flat=True))
    sources,summary,digest=inspect(user.company,units,target)
    if digest!=data.get('digest'):raise ValidationError('Единицы или связанные записи изменились. Проверьте новый предпросмотр перед объединением.')
    for model in MODELS:model.objects.filter(company=user.company,unit__in=symbols).update(unit=target.symbol)
    MeasurementUnitAlias.objects.filter(company=user.company,target__in=sources).update(target=target)
    for source in sources:MeasurementUnitAlias.objects.update_or_create(company=user.company,symbol=source.symbol,defaults={'target':target})
    from apps.audit.models import AuditLog
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='works.MeasurementUnit',entity_id=str(target.pk),old_value={'sources':[{'id':u.pk,'symbol':u.symbol,'name':u.name} for u in sources]},new_value={'target':target.symbol,'updated':summary},comment='Объединены равнозначные единицы измерения, объёмы и цены сохранены')
    MeasurementUnit.objects.filter(company=user.company,pk__in=[u.pk for u in sources]).delete()
    return target


class UnitMerge(View):
    def display(self,request,bound=False):
        require_roles(request.user,PLAN_ROLES)
        error=''
        if bound and request.POST.get('confirm'):
            try:
                target=merge(request.user,request.POST.get('selection',''))
                messages.success(request,f'Единицы объединены в «{target.symbol}». Объёмы и цены сохранены.')
                return redirect('works:unit_list')
            except ValidationError as exc:error='; '.join(exc.messages)
        selected=request.POST.getlist('selected') if bound else request.GET.getlist('selected')
        if request.GET.get('source'):selected=[request.GET['source']]
        initial={'units':selected}
        if len(selected)>1:
            initial['target']=MeasurementUnit.objects.filter(company=request.user.company,pk__in=[pk for pk in selected if pk.isdigit()]).order_by('pk').values_list('pk',flat=True).first()
        form=MergeForm(request.POST if bound and request.POST.get('stage')=='preview' else None,company=request.user.company,initial=initial)
        context={'form':form,'error':error}
        if bound and request.POST.get('stage')=='preview' and form.is_valid():
            target=form.cleaned_data['target'];sources,summary,digest=inspect(request.user.company,list(form.cleaned_data['units']),target)
            context.update(target=target,sources=sources,summary=summary,selection=signing.dumps({'user':request.user.pk,'company':request.user.company_id,'sources':[u.pk for u in sources],'target':target.pk,'digest':digest},salt=SALT,compress=True))
        return render(request,'works/unit_merge.html',context,status=400 if error else 200)

    def get(self,request):return self.display(request)
    def post(self,request):return self.display(request,True)
