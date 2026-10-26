from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import scope_queryset, require_roles, READ_ROLES, PLAN_ROLES
from apps.projects.models import ConstructionObject, Section
from .models import ComplexObject, ProjectWork, ProjectWorkItem


class ComplexObjectForm(forms.ModelForm):
    class Meta:
        model=ComplexObject
        fields=['construction_object','section','name','description']

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=user.company
        self.fields['construction_object'].queryset=scope_queryset(ConstructionObject.objects.all(),user)
        self.fields['section'].queryset=scope_queryset(Section.objects.all(),user)
        self.sections=list(self.fields['section'].queryset.values('id','construction_object_id','name'))


class ComplexObjectCatalogue(View):
    def display(self,request,pk=None,bound=False):
        require_roles(request.user,PLAN_ROLES if bound or pk else READ_ROLES)
        instance=get_object_or_404(scope_queryset(ComplexObject.objects.all(),request.user),pk=pk) if pk else ComplexObject(company=request.user.company)
        form=ComplexObjectForm(request.POST if bound else None,user=request.user,instance=instance)
        if bound and form.is_valid():
            try:
                form.instance._section_actor=request.user
                form.save()
            except (ValidationError,IntegrityError) as exc:
                form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Конфликт записей: изменение отменено.')
            else:
                messages.success(request,'Комплексный объект сохранен. Его количество всегда 1 шт.')
                return redirect('works:complex_object_list')
        return render(request,'works/complex_object_catalogue.html',{'form':form,'complex_objects':scope_queryset(ComplexObject.objects.all(),request.user).select_related('construction_object').prefetch_related('work_packages'),'editing':pk})
    def get(self,request,pk=None):return self.display(request,pk)
    def post(self,request,pk=None):return self.display(request,pk,True)


class ComplexObjectDetailForm(forms.ModelForm):
    class Meta:
        model=ComplexObject
        fields=['name','description','section']

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['section'].queryset=Section.objects.filter(company_id=self.instance.company_id,construction_object_id=self.instance.construction_object_id)
        self.fields['section'].help_text='При смене раздела все комплексы и работы переносятся вместе с комплексным объектом.'


class ComplexObjectFilterForm(forms.Form):
    package=forms.ModelChoiceField(queryset=ProjectWork.objects.none(),required=False,label='Комплекс работ',empty_label='Все комплексные работы')
    item=forms.ModelChoiceField(queryset=ProjectWorkItem.objects.none(),required=False,label='Работа',empty_label='Все работы')

    def __init__(self,*args,object,**kwargs):
        super().__init__(*args,**kwargs)
        packages=object.work_packages.filter(company_id=object.company_id,merged_source__isnull=True)
        self.fields['package'].queryset=packages.order_by('name','pk')
        items=ProjectWorkItem.objects.filter(company_id=object.company_id,project_work__in=packages).select_related('project_work')
        package=self.data.get('package','')
        if package:items=items.filter(project_work_id=package) if str(package).isdigit() else items.none()
        self.fields['item'].queryset=items.order_by('name','pk')
        self.fields['item'].label_from_instance=lambda item:f'{item.name} · {item.project_work.name}'
        for field in self.fields.values():field.widget.attrs['class']='form-select'


from django.contrib.auth.decorators import login_required
from .navigation import return_path


@login_required
def complex_object_detail(request,pk):
    require_roles(request.user,READ_ROLES if request.method in {'GET','HEAD'} else PLAN_ROLES)
    if request.method not in {'GET','HEAD','POST'}:
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['GET','HEAD','POST'])
    obj=get_object_or_404(scope_queryset(ComplexObject.objects.select_related('construction_object__project'),request.user),pk=pk)
    form=ComplexObjectDetailForm(request.POST if request.method=='POST' else None,instance=obj)
    if request.method=='POST' and form.is_valid():
        from django.db import transaction
        from apps.audit.models import AuditLog
        try:
            with transaction.atomic():
                ConstructionObject.objects.select_for_update().get(pk=obj.construction_object_id,company=request.user.company)
                previous=ComplexObject.objects.select_for_update().get(pk=obj.pk)
                form.instance._section_actor=request.user
                form.save()
                AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='works.ComplexObject',entity_id=str(obj.pk),old_value={'name':previous.name,'description':previous.description,'section_id':previous.section_id},new_value={'name':obj.name,'description':obj.description,'section_id':obj.section_id},comment='Изменение карточки комплексного объекта')
        except (ValidationError,IntegrityError) as exc:
            form.add_error(None,'; '.join(exc.messages) if isinstance(exc,ValidationError) else 'Конфликт записей: изменение отменено.')
        else:
            messages.success(request,'Комплексный объект сохранён.')
            return redirect('works:complex_object_detail',pk=obj.pk)
    mode=request.GET.get('view',request.session.get('complex_object_view','tree'))
    if mode not in {'table','tree'}:mode='tree'
    request.session['complex_object_view']=mode
    works=list(obj.work_packages.filter(company=request.user.company,merged_source__isnull=True).select_related('section','complex_object','section__construction_object__project').prefetch_related('items').order_by('section__name','code','pk'))
    work_total=len(works)
    filter_form=ComplexObjectFilterForm(request.GET,object=obj)
    if filter_form.is_valid():
        selected_package=filter_form.cleaned_data['package']
        selected_item=filter_form.cleaned_data['item']
        if selected_package:works=[work for work in works if work.pk==selected_package.pk]
        if selected_item:works=[work for work in works if work.pk==selected_item.project_work_id]
        for work in works:work.catalogue_items=[item for item in work.items.all() if not selected_item or item.pk==selected_item.pk]
    else:works=[]
    view_links={}
    for view in ['table','tree']:
        params=request.GET.copy();params['view']=view
        view_links[view+'_view_query']=params.urlencode()
    sections={}
    for work in works:
        work.items_count=len(work.items.all())
        sections.setdefault(work.section_id,{'section':work.section,'branches':[{'object':obj,'works':[]}]})['branches'][0]['works'].append(work)
    return render(request,'works/complex_object_detail.html',{**view_links,'object':obj,'form':form,'filter_form':filter_form,'work_total':work_total,'works':works,'view_mode':mode,'tree_groups':[{'object':obj.construction_object,'sections':list(sections.values())}],'bulk_route':'works:bulk_delete','bulk_kind':'works','bulk_contextual':True,
        'catalogue_return':return_path(request),
        'catalogue_reveal':bool(request.GET.get('package') or request.GET.get('item')),'search_query':''})
