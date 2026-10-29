"""Signed row baselines: concurrent dispatchers may merge changes to different rows."""
from decimal import Decimal, InvalidOperation
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError

SALT='production-dispatcher-rows-v1'


def normalized(field, value):
    if isinstance(field,forms.BooleanField):return bool(value and str(value).lower() not in {'false','0'})
    if isinstance(field,forms.MultipleChoiceField):return sorted(str(item) for item in (value or []))
    if isinstance(field,forms.DecimalField) and value not in (None,''):
        try:return str(Decimal(str(value)).normalize())
        except InvalidOperation:return str(value)
    if isinstance(field,forms.IntegerField) and value not in (None,''):
        try:return str(int(value))
        except (ValueError,TypeError):return str(value)
    return '' if value in (None,'') else str(value)


def snapshot(user,obj,day,groups):
    data={'user':user.pk,'company':obj.company_id,'object':obj.pk,'day':day.isoformat(),'rows':{}}
    data['actuation']=actuation_version(obj,day)
    from .day_reset import epoch
    data['reset_epoch']=epoch(obj,day)
    for group in groups:
        if group['prefix'] not in {'works','labor'}:continue
        entries={}
        for form in group['formset']:
            marker='pk:'+str(form.instance.pk) if form.instance.pk else form.prefix
            entries[marker]={'version':form.instance.updated_at.isoformat() if form.instance.pk else None,
                'values':{name:normalized(field,form[name].value()) for name,field in form.fields.items() if name not in {'work_claim_token','work_editing','person_claim_token','person_editing'}}}
        data['rows'][group['prefix']]=entries
    return signing.dumps(data,salt=SALT,compress=True)


def prepare(user,obj,day,groups,signed):
    try:data=signing.loads(signed,salt=SALT,max_age=12*3600)
    except (signing.BadSignature,TypeError):raise ValidationError('Версия формы устарела. Обновите день; сохранённый факт останется на месте.')
    if any(data.get(key)!=value for key,value in {'user':user.pk,'company':obj.company_id,'object':obj.pk,'day':day.isoformat()}.items()):
        raise ValidationError('Эта форма относится к другому пользователю, объекту или дню.')
    from .day_reset import epoch
    if data.get('reset_epoch',0)!=epoch(obj,day):raise ValidationError('Производственная часть сброшена администратором. Обновите страницу перед новым вводом.')
    for group in groups:
        if group['prefix'] not in {'works','labor'}:continue
        originals=data['rows'].get(group['prefix'],{})
        for form in group['formset']:
            pk=form['id'].value()
            marker='pk:'+str(pk) if pk else form.prefix
            original=originals.get(marker)
            if pk and original is None:raise ValidationError('В форме появилась чужая строка. Обновите день.')
            posted={name:normalized(field,form[name].value()) for name,field in form.fields.items() if name not in {'work_claim_token','work_editing','person_claim_token','person_editing'}}
            inactive=form.data.get(form.add_prefix('work_editing' if group['prefix']=='works' else 'person_editing'))=='0'
            original_values=original['values'] if original else None
            if group['prefix']=='labor' and form.data.get('collaborative')=='1':
                relation_names={'work','work_item','general','work_mode','work_targets'}
                posted={k:v for k,v in posted.items() if k not in relation_names}
                original_values={k:v for k,v in original_values.items() if k not in relation_names} if original_values else None
            untouched=inactive or original and posted==original_values and not getattr(form,'prepared_from_previous',False)
            if untouched:
                form.empty_permitted=True
                form.has_changed=lambda:False
                form.changed_data=[]
            elif pk:
                form.expected_entry_version=original['version']
    return data


def actuation_version(obj,day):
    from .models import DayActuation
    record=DayActuation.objects.filter(company=obj.company,construction_object=obj,date=day).first()
    return record.updated_at.isoformat() if record else None


def saved_state(user,obj,day,groups,baseline):
    """Rebase only rows saved by this request; keep stale colleague baselines stale."""
    baseline['actuation']=actuation_version(obj,day)
    result=[]
    for group in groups:
        prefix=group['prefix']
        if prefix not in {'works','labor'}:continue
        entries=baseline['rows'][prefix]
        baseline['rows'][prefix]={key:value for key,value in entries.items() if key.startswith('pk:')}
        entries=baseline['rows'][prefix]
        for form in group['formset']:
            if not form.cleaned_data or not form.has_changed():continue
            original=form['id'].value()
            if original:entries.pop('pk:'+str(original),None)
            deleted=bool(form.cleaned_data.get('DELETE'))
            identity=form.instance.pk if not deleted else None
            result.append({'prefix':form.prefix,'id':identity,'deleted':deleted})
            if identity:
                values={name:normalized(field,form[name].value()) for name,field in form.fields.items() if name not in {'work_claim_token','work_editing','person_claim_token','person_editing'}}
                values['id']=str(identity)
                entries['pk:'+str(identity)]={'version':form.instance.updated_at.isoformat(),'values':values}
    return {'production_snapshot':signing.dumps(baseline,salt=SALT,compress=True),'rows':result}


def check_locked(groups,obj=None,day=None,baseline=None):
    if obj and baseline is not None:
        from .day_reset import epoch
        if baseline.get('reset_epoch',0)!=epoch(obj,day):raise ValidationError('Производственная часть сброшена администратором. Обновите страницу перед новым вводом.')
    if obj and baseline is not None and baseline.get('actuation')!=actuation_version(obj,day):
        raise ValidationError('Актировка объекта изменилась. Обновите день перед сохранением.')
    for group in groups:
        forms_changed=[form for form in group['formset'] if hasattr(form,'expected_entry_version')]
        if not forms_changed:continue
        current=dict(group['formset'].model.objects.filter(pk__in=[form.instance.pk for form in forms_changed]).values_list('pk','updated_at'))
        for form in forms_changed:
            stamp=current.get(form.instance.pk)
            if stamp is None or stamp.isoformat()!=form.expected_entry_version:
                raise ValidationError('Эту строку уже изменили в другой вкладке или другой диспетчер. Обновите день перед повторным изменением; данные не перезаписаны.')


# SQLite cannot lock a row and its shared-cache test database fails immediately
# when another connection reads a table being written. PostgreSQL keeps its
# ordinary object/employee row locks; the single-process SQLite host serializes
# production requests, including their baseline reads, before starting a write.
from functools import wraps
from threading import RLock
from django.db import connection
_sqlite_mutex=RLock()


def sqlite_serialized(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        if connection.vendor=='sqlite':
            with _sqlite_mutex:return function(*args,**kwargs)
        return function(*args,**kwargs)
    return wrapped
