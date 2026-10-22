from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, render, redirect
from django.views import View
from core.permissions import scope_queryset, require_roles, READ_ROLES, PLAN_ROLES
from apps.projects.models import ConstructionObject
from .models import ComplexObject


class ComplexObjectForm(forms.ModelForm):
    class Meta:
        model=ComplexObject
        fields=['construction_object','name','description']

    def __init__(self,*args,user,**kwargs):
        super().__init__(*args,**kwargs)
        self.instance.company=user.company
        self.fields['construction_object'].queryset=scope_queryset(ConstructionObject.objects.all(),user)


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
