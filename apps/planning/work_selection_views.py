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
    def __init__(self,*args,version,**kwargs):
        super().__init__(*args,**kwargs)
        from .bulk_add import editable_months
        self.works=ProjectWork.objects.filter(company=version.company,section__construction_object=version.construction_object,merged_source__isnull=True)
        self.fields['selection'].choices=choices(self.works)
        self.fields['selection'].widget.attrs.update({'data-native-select':'1','class':'form-select'})
        parents={row.pk:row for row in self.works.select_related('complex_object')}
        kinds={pk:row.kind for pk,row in parents.items()}
        from apps.works.models import ProjectWorkItem
        item_parents=dict(ProjectWorkItem.objects.filter(project_work__in=self.works,company=version.company).values_list('pk','project_work_id'))
        self.catalogue_options=[]
        for group in self.fields['selection'].choices:
            if not isinstance(group[1],(list,tuple)):continue
            for value,label in group[1]:
                prefix,pk=value.split(':')
                level='object' if prefix=='object' else 'package' if prefix=='package' and kinds[int(pk)]=='COMPOSITE' else 'work'
                work=parents.get(int(pk)) if prefix=='package' else parents.get(item_parents.get(int(pk))) if prefix=='item' else None
                self.catalogue_options.append({'value':value,'label':label,'level':level,
                    'object_id':str(pk) if prefix=='object' else str(work.complex_object_id or '') if work else '',
                    'package_id':str(work.pk) if work and work.kind=='COMPOSITE' else ''})

        self.fields['months'].choices=[(month.isoformat(),month.strftime('%m.%Y')) for month in editable_months(version)]

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
    form=BranchForm(request.POST,version=version)
    try:
        if not form.is_valid():raise ValidationError('Выберите доступную позицию и месяцы.')
        branch=resolve(form.works,form.cleaned_data['selection'])
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
