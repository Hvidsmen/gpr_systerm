"""Convert catalogue grouping levels without replacing work IDs or history."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.core import signing
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction, IntegrityError
from django.shortcuts import get_object_or_404, render, redirect
from core.permissions import require_roles, scope_queryset, PLAN_ROLES
from apps.projects.models import Section, ConstructionObject
from apps.audit.models import AuditLog
from .models import ComplexObject, ProjectWork

SALT='works.group-conversion.v1'


class ConversionForm(forms.Form):
    name=forms.CharField(label='Название после преобразования', max_length=255)
    target_section=forms.ModelChoiceField(queryset=Section.objects.none(),label='Раздел для комплексов нового комплексного объекта',required=False)
    confirm=forms.BooleanField(label='Подтверждаю преобразование перечисленных работ')

    def __init__(self,*args,user,source,kind,**kwargs):
        super().__init__(*args,**kwargs)
        self.initial['name']=source.name
        if kind=='section':
            self.fields['target_section'].queryset=scope_queryset(Section.objects.filter(construction_object_id=source.construction_object_id),user).exclude(pk=source.pk)
            self.fields['target_section'].required=True
        else:self.fields.pop('target_section')


def source_and_works(user,kind,pk):
    require_roles(user,PLAN_ROLES)
    if kind not in {'object','section'}:raise PermissionDenied
    model=ComplexObject if kind=='object' else Section
    source=get_object_or_404(scope_queryset(model.objects.all(),user),pk=pk)
    works=ProjectWork.objects.filter(**{'complex_object':source} if kind=='object' else {'section':source}).select_related('section','complex_object').order_by('pk')
    if any(w.company_id!=user.company_id or w.section.construction_object_id!=source.construction_object_id for w in works):raise PermissionDenied('Обнаружены недоступные связи.')
    return source,list(works)


def fingerprint(source,works):
    return {'source':source.pk,'name':source.name,'description':source.description,'object':source.construction_object_id,
            'works':[(w.pk,w.section_id,w.complex_object_id,w.code,w.kind,w.name) for w in works]}


@transaction.atomic
def convert(user,kind,pk,name,target_section,token):
    source,works=source_and_works(user,kind,pk)
    ConstructionObject.objects.select_for_update().get(pk=source.construction_object_id,company=user.company)
    type(source).objects.select_for_update().get(pk=pk)
    source,works=source_and_works(user,kind,pk)
    list(ProjectWork.objects.select_for_update().filter(pk__in=[w.pk for w in works]))
    source,works=source_and_works(user,kind,pk)
    try:payload=signing.loads(token,salt=SALT,max_age=900)
    except signing.BadSignature:raise ValidationError('Просмотр устарел. Проверьте состав повторно.')
    current={'user':user.pk,'company':user.company_id,'kind':kind,'data':fingerprint(source,works)}
    # JSON roundtrip normalizes tuple values in the fingerprint.
    import json
    if payload!=json.loads(json.dumps(current)):raise ValidationError('Состав изменился. Проверьте обновлённый список.')
    name=name.strip()
    if not name:raise ValidationError('Укажите название.')
    if kind=='object':
        if any(existing.strip().casefold()==name.casefold() for existing in Section.objects.filter(construction_object_id=source.construction_object_id).values_list('name',flat=True)):raise ValidationError('Раздел с таким названием уже есть. Укажите другое название.')
        destination=Section(company=user.company,construction_object_id=source.construction_object_id,name=name,description=source.description)
        destination.save()
        ProjectWork.objects.filter(pk__in=[w.pk for w in works]).update(section=destination,complex_object=None)
    else:
        if source.complex_objects.exists():raise ValidationError('В разделе есть комплексные объекты. Сначала преобразуйте или перенесите их.')
        if any(w.kind!='COMPOSITE' for w in works):raise ValidationError('В разделе есть самостоятельные работы. Перенесите их в другой раздел: комплексный объект может содержать только комплексные работы.')
        if any(w.complex_object_id for w in works):raise ValidationError('В разделе уже есть работы других комплексных объектов. Сначала перенесите их в другой раздел.')
        target=get_object_or_404(scope_queryset(Section.objects.select_for_update(),user),pk=getattr(target_section,'pk',None),construction_object_id=source.construction_object_id)
        if target.pk==source.pk:raise ValidationError('Выберите другой раздел.')
        if any(existing.strip().casefold()==name.casefold() for existing in ComplexObject.objects.filter(construction_object_id=source.construction_object_id).values_list('name',flat=True)):raise ValidationError('Такой комплексный объект уже существует. Укажите другое название.')
        codes=[w.code for w in works]
        if ProjectWork.objects.filter(section=target,code__in=codes).exists():raise ValidationError('В целевом разделе есть работы с совпадающими кодами. Выберите другой раздел.')
        destination=ComplexObject(company=user.company,construction_object_id=source.construction_object_id,section=target,name=name,description=source.description)
        destination.full_clean();destination.save()
        ProjectWork.objects.filter(pk__in=[w.pk for w in works]).update(section=target,complex_object=destination)
    old=fingerprint(source,works)
    source.delete()
    AuditLog.objects.create(user=user,action='UPDATE',entity_type='works.GroupConversion',entity_id=str(destination.pk),old_value=old,new_value={'kind':kind,'target':destination.pk,'name':name},comment='Преобразование комплексного объекта и раздела без изменения работ, планов и факта')
    return destination


@login_required
@require_http_methods(['GET','POST'])
def conversion(request,kind,pk):
    source,works=source_and_works(request.user,kind,pk)
    form=ConversionForm(request.POST or None,user=request.user,source=source,kind=kind)
    error=None
    if request.method=='POST' and form.is_valid():
        try:
            convert(request.user,kind,pk,form.cleaned_data['name'],form.cleaned_data.get('target_section'),request.POST.get('token',''))
            messages.success(request,'Преобразование выполнено. Работы, нормативы, планы и факт сохранены.')
            return redirect('works:work_list')
        except (ValidationError,IntegrityError) as exc:
            error='; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Есть конфликт связей. Преобразование отменено целиком.'
            source,works=source_and_works(request.user,kind,pk)
    token=signing.dumps({'user':request.user.pk,'company':request.user.company_id,'kind':kind,'data':fingerprint(source,works)},salt=SALT,compress=True)
    return render(request,'works/conversion.html',{'source':source,'works':works,'kind':kind,'form':form,'token':token,'error':error})
