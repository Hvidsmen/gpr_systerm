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
            untouched=inactive or original and posted==original['values'] and not getattr(form,'prepared_from_previous',False)
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


def check_locked(groups,obj=None,day=None,baseline=None):
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
