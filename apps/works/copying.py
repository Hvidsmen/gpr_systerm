"""Copy catalogue structure; operational records never follow the copy."""
from copy import deepcopy
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404, redirect, render
from django.http import Http404
from core.permissions import PLAN_ROLES, require_roles, scope_queryset
from apps.projects.models import ConstructionObject
from apps.audit.models import AuditLog
from .models import ComplexObject, ProjectWork, ProjectWorkItem
from .prices import price_on

KINDS={'object':(ComplexObject,'комплексный объект'),'package':(ProjectWork,'комплексную работу / самостоятельную работу'),'item':(ProjectWorkItem,'работу в составе комплекса')}

class CopyForm(forms.Form):
    name=forms.CharField(label='Новое название',max_length=255,widget=forms.TextInput(attrs={'class':'form-control','autofocus':True}))


def values(instance,exclude=()):
    omitted={'id','created_at','updated_at','code','excel_aliases',*exclude}
    return {field.attname:deepcopy(getattr(instance,field.attname)) for field in instance._meta.concrete_fields if field.name not in omitted and not field.primary_key}


def unique_name(queryset,name):
    if any(existing.strip().casefold()==name.casefold() for existing in queryset.values_list('name',flat=True)):
        raise ValidationError({'name':'Такое название уже есть в этом месте. Укажите другое.'})


def clone_items(source_items,work,*,root=None,name=None):
    """Remap parent IDs without recursion; reject invalid/cyclic source trees."""
    pending={row.pk:row for row in source_items};copied={};result=None
    sequence=(work.items.aggregate(last=Max('sequence'))['last'] or 0)+1
    while pending:
        ready=[row for row in pending.values() if (root is not None and row.pk==root.pk) or row.parent_id is None or row.parent_id in copied]
        if not ready:raise ValidationError('В исходной структуре есть цикл или недоступный родитель.')
        for source in ready:
            if source.company_id!=work.company_id:raise ValidationError('Исходная работа относится к другой компании.')
            data=values(source,exclude={'project_work','parent','planned_quantity','sequence'})
            parent_id=(root.parent_id if root and source.pk==root.pk else copied[source.parent_id].pk if source.parent_id else None)
            if root and source.pk==root.pk:data['name']=name
            clone=ProjectWorkItem(**data,project_work=work,parent_id=parent_id,planned_quantity=0,sequence=sequence)
            clone.full_clean();clone.save();sequence+=1
            copied[source.pk]=clone;pending.pop(source.pk)
            if root and source.pk==root.pk:result=clone
    return result,len(copied)


def clone_package(source,*,name,complex_object,user):
    if hasattr(source,'merged_source'):raise ValidationError('Архивный источник объединения не копируется. Выберите действующую работу.')
    data=values(source,exclude={'complex_object','name','unit_price','status'})
    clone=ProjectWork(**data,name=name,complex_object=complex_object,unit_price=price_on(source),status='PLANNED')
    clone._price_actor=user
    clone.full_clean();clone.save()
    _,count=clone_items(list(source.items.order_by('sequence','pk')),clone)
    return clone,count


@transaction.atomic
def copy_catalogue(user,kind,pk,name):
    require_roles(user,PLAN_ROLES)
    if kind not in KINDS:raise Http404
    source=get_object_or_404(scope_queryset(KINDS[kind][0].objects.all(),user),pk=pk)
    name=name.strip()
    if not name or len(name)>255:raise ValidationError({'name':'Укажите новое название до 255 символов.'})
    object_id=source.construction_object_id if kind=='object' else source.section.construction_object_id if kind=='package' else source.project_work.section.construction_object_id
    ConstructionObject.objects.select_for_update().get(pk=object_id,company=user.company)
    # Re-read after locking; no changes are made to the source.
    source=get_object_or_404(scope_queryset(KINDS[kind][0].objects.all(),user).select_for_update(),pk=pk)
    count=0
    if kind=='object':
        unique_name(ComplexObject.objects.filter(company=user.company,construction_object_id=object_id),name)
        clone=ComplexObject(**values(source,exclude={'name'}),name=name)
        clone.full_clean();clone.save()
        packages=list(source.work_packages.order_by('pk'))
        for package in packages:
            if package.company_id!=user.company_id:raise ValidationError('Комплекс работ другой компании.')
            _,items=clone_package(package,name=package.name,complex_object=clone,user=user);count+=items
    elif kind=='package':
        unique_name(ProjectWork.objects.filter(company=user.company,section=source.section,complex_object=source.complex_object),name)
        clone,count=clone_package(source,name=name,complex_object=source.complex_object,user=user)
    else:
        work=source.project_work
        unique_name(work.items.filter(parent_id=source.parent_id),name)
        items=list(work.items.order_by('sequence','pk'));selected={source.pk};children={}
        for item in items:children.setdefault(item.parent_id,[]).append(item.pk)
        stack=[source.pk]
        while stack:
            for child in children.get(stack.pop(),[]):
                if child in selected:raise ValidationError('В исходной структуре есть цикл.')
                selected.add(child);stack.append(child)
        clone,count=clone_items([item for item in items if item.pk in selected],work,root=source,name=name)
    AuditLog.objects.create(user=user,action='CREATE',entity_type=clone._meta.label,entity_id=str(clone.pk),new_value={'copied_from':source.pk,'kind':kind,'name':name,'copied_items':count},comment='Копирование справочника работ')
    return clone


@login_required
def copy_view(request,kind,pk):
    require_roles(request.user,PLAN_ROLES)
    if kind not in KINDS:raise Http404
    source=get_object_or_404(scope_queryset(KINDS[kind][0].objects.all(),request.user),pk=pk)
    form=CopyForm(request.POST or None,initial={'name':(source.name[:247]+' — копия')})
    if request.method=='POST' and form.is_valid():
        try:
            clone=copy_catalogue(request.user,kind,pk,form.cleaned_data['name'])
            messages.success(request,'Копия создана: '+clone.name)
            if kind=='object':return redirect('works:complex_object_list')
            return redirect('works:work_detail',pk=clone.project_work_id if kind=='item' else clone.pk)
        except ValidationError as exc:
            if hasattr(exc,'message_dict'):
                for field,errors in exc.message_dict.items():
                    for error in errors:form.add_error('name' if field=='name' else None,error)
            else:form.add_error(None,exc)
    return render(request,'works/copy.html',{'form':form,'source':source,'kind':kind,'kind_label':KINDS[kind][1]})
