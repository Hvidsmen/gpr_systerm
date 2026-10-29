"""Edit the shared company group order in its existing catalogue."""
from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect,render
from core.permissions import require_roles,PERSONNEL_CATALOG_EDIT_ROLES,EQUIPMENT_CATALOG_EDIT_ROLES
from apps.audit.models import AuditLog
from .models import BrigadeGroup,EquipmentCategory
from .group_order import ordered


class OrderForm(forms.Form):
    id=forms.IntegerField(widget=forms.HiddenInput)
    sort_order=forms.IntegerField(label='Порядок',min_value=0,max_value=1000000,
        widget=forms.NumberInput(attrs={'class':'form-control form-control-sm','style':'width:100px'}))


def context(company,kind,data=None):
    model=EquipmentCategory if kind=='equipment' else BrigadeGroup
    rows=list(ordered(model.objects.filter(company=company),kind=kind))
    Factory=forms.formset_factory(OrderForm,extra=0,max_num=10000,validate_max=True)
    formset=Factory(data,prefix='order',initial=[{'id':row.pk,'sort_order':row.sort_order} for row in rows])
    labels={row.pk:row.name for row in rows}
    for form in formset:
        raw=form['id'].value()
        form.group_label=labels.get(int(raw),'Группа') if str(raw or '').isdigit() else 'Группа'
    return {'order_formset':formset,'order_rows':rows}


def save(request,kind):
    require_roles(request.user,EQUIPMENT_CATALOG_EDIT_ROLES if kind=='equipment' else PERSONNEL_CATALOG_EDIT_ROLES)
    with transaction.atomic():
        model=EquipmentCategory if kind=='equipment' else BrigadeGroup
        rows=list(model.objects.select_for_update().filter(company=request.user.company).order_by('pk'))
        ctx=context(request.user.company,kind,request.POST)
        formset=ctx['order_formset']
        if formset.is_valid():
            submitted=[f.cleaned_data for f in formset]
            if len({r['id'] for r in submitted})!=len(submitted) or {r['id'] for r in submitted}!={r.pk for r in rows}:
                formset._non_form_errors=formset.error_class(['Состав групп изменился. Обновите страницу и задайте порядок снова.'])
            else:
                priorities={r['id']:r['sort_order'] for r in submitted}
                before={str(row.pk):row.sort_order for row in rows}
                for row in rows:row.sort_order=priorities[row.pk]
                model.objects.bulk_update(rows,['sort_order'])
                AuditLog.objects.create(user=request.user,action='UPDATE',entity_type=model._meta.label,entity_id='order',
                    old_value=before,new_value={str(pk):priority for pk,priority in priorities.items()},comment='Изменён общий порядок групп')
                messages.success(request,'Порядок групп сохранён. Он применяется в справочниках, планах, факте и отчётах.')
                return redirect('resources:equipment_category_list' if kind=='equipment' else 'resources:brigade_group_list')
    return render(request,'resources/group_order_form.html',{**ctx,'group_order_kind':kind},status=400)
