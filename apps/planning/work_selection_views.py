from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.shortcuts import redirect
from core.permissions import PLAN_ROLES,require_roles,require_planning_object
from apps.works.models import ProjectWork
from .models import GlobalPlanVersion,WorkMonthAllocation
from .work_selection import choices,resolve,extend_allocation

class BranchForm(forms.Form):
    selection=forms.ChoiceField(label='Добавить объект, комплекс или работу')
    months=forms.MultipleChoiceField(label='Месяцы',widget=forms.CheckboxSelectMultiple)
    def __init__(self,*args,version,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        from .bulk_add import editable_months
        self.works=ProjectWork.objects.filter(company=version.company,section__construction_object=version.construction_object,merged_source__isnull=True)
        self.fields['selection'].choices=choices(self.works,user=user)
        self.fields['selection'].widget.attrs.update({'data-native-select':'1','class':'form-select'})
        from .plan_workbench import catalogue_options
        self.catalogue_options=catalogue_options(self.works,self.fields['selection'].choices)

        self.fields['months'].choices=[(month.isoformat(),month.strftime('%m.%Y')) for month in editable_months(version)]


class ScheduleBranchForm(forms.Form):
    selection=forms.ChoiceField(label='Добавить объект, комплекс или работу')
    def __init__(self,*args,schedule,user=None,**kwargs):
        super().__init__(*args,**kwargs)
        from .estimate_catalog import alternatives
        from .plan_workbench import catalogue_options
        works=alternatives(schedule)
        self.fields['selection'].choices=choices(works,include_construction_object=True,user=user)
        self.fields['selection'].widget.attrs.update({'data-native-select':'1','class':'form-select'})
        self.catalogue_options=catalogue_options(works,self.fields['selection'].choices)

@login_required
@transaction.atomic
def add_branch(request,pk):
    require_roles(request.user,PLAN_ROLES)
    from .workspace_views import version_for
    version=version_for(request,pk)
    require_planning_object(request.user,version.construction_object_id)
    if request.method!='POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    version=GlobalPlanVersion.objects.select_for_update().select_related('workspace').get(pk=version.pk)
    if version.status not in {'DRAFT','REJECTED'}:raise PermissionDenied('Состав меняется только в черновике.')
    form=BranchForm(request.POST,version=version,user=request.user)
    try:
        if not form.is_valid():raise ValidationError('Выберите доступную позицию и месяцы.')
        branch=resolve(form.works,form.cleaned_data['selection'],user=request.user)
        from datetime import date
        with transaction.atomic():
            for work,selected in branch:
                for month in form.cleaned_data['months']:
                    row,created=WorkMonthAllocation.objects.get_or_create(company=version.company,version=version,work=work,month=date.fromisoformat(month),defaults={'selected_item_ids':selected})
                    if not created:extend_allocation(row,selected)
            version.snapshot={};version.save(update_fields=['snapshot'])
        messages.success(request,'Выбранная ветка добавлена. Существующие объёмы сохранены.')
    except ValidationError as exc:messages.error(request,'; '.join(exc.messages))
    return redirect('planning:workspace_edit',pk=version.pk)
