from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import scope_queryset, require_roles, READ_ROLES, PLAN_ROLES
from apps.projects.models import ConstructionObject, Section
from .models import ComplexObject


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
            form.save();messages.success(request,'Комплексный объект сохранен. Его количество всегда 1 шт.')
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
        if self.instance.work_packages.exists():
            self.fields['section'].disabled=True
            self.fields['section'].help_text='Раздел закреплён за комплексным объектом и его работами.'


from django.contrib.auth.decorators import login_required


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
        with transaction.atomic():
            previous=ComplexObject.objects.select_for_update().get(pk=obj.pk)
            form.save()
            AuditLog.objects.create(user=request.user,action='UPDATE',entity_type='works.ComplexObject',entity_id=str(obj.pk),old_value={'name':previous.name,'description':previous.description},new_value={'name':obj.name,'description':obj.description},comment='Изменение карточки комплексного объекта')
        messages.success(request,'Комплексный объект сохранён.')
        return redirect('works:complex_object_detail',pk=obj.pk)
    mode=request.GET.get('view',request.session.get('complex_object_view','tree'))
    if mode not in {'table','tree'}:mode='tree'
    request.session['complex_object_view']=mode
    works=list(obj.work_packages.filter(company=request.user.company,merged_source__isnull=True).select_related('section','complex_object','section__construction_object__project').prefetch_related('items').order_by('section__name','code','pk'))
    sections={}
    for work in works:
        work.items_count=len(work.items.all())
        sections.setdefault(work.section_id,{'section':work.section,'branches':[{'object':obj,'works':[]}]})['branches'][0]['works'].append(work)
    return render(request,'works/complex_object_detail.html',{'object':obj,'form':form,'works':works,'view_mode':mode,'tree_groups':[{'object':obj.construction_object,'sections':list(sections.values())}],'bulk_route':'works:bulk_delete','bulk_kind':'works'})
