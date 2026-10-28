"""Recover a missing actual departure without inventing a camp arrival."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from core.fact_history import history_actor
from .models import Person,Camp,HousingEvent,CampStay
from .views import access


class DepartureForm(forms.Form):
    date=forms.DateField(label='Фактическая дата выезда',widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
    camp=forms.ModelChoiceField(label='Из какого ВЖГ выехал',queryset=Camp.objects.none())
    comment=forms.CharField(label='Основание уточнения',max_length=4000,widget=forms.Textarea(attrs={'rows':2}))
    version=forms.CharField(required=False,widget=forms.HiddenInput)
    def __init__(self,*args,company,**kwargs):
        super().__init__(*args,**kwargs);self.fields['camp'].queryset=Camp.objects.filter(company=company)
    def clean_date(self):
        day=self.cleaned_data['date']
        if day>timezone.localdate():raise forms.ValidationError('Фактический выезд нельзя оформить будущей датой.')
        return day


@login_required
@require_http_methods(['GET','POST'])
def edit(request,pk):
    access(request);person=get_object_or_404(Person,company=request.user.company,pk=pk)
    existing=HousingEvent.objects.filter(company=request.user.company,person=person,kind='DEPARTURE',stay=None).order_by('-created_at','-pk').first()
    form=DepartureForm(request.POST or None,company=request.user.company,initial={'date':existing.date if existing else None,'camp':existing.from_camp_id if existing else None,'version':existing.updated_at.isoformat() if existing else '', 'comment':existing.comment if existing else ''})
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic(),history_actor(request.user,'Уточнение начальной истории выезда'):
                from apps.accounts.models import Company
                Company.objects.filter(pk=request.user.company_id).update(name=F('name'))
                Person.objects.select_for_update().get(pk=person.pk,company=request.user.company)
                if CampStay.objects.filter(company=request.user.company,person=person,cancelled_at=None).exists():raise ValidationError('История размещения уже оформлена. Исправьте выезд через существующую запись; начальное уточнение не сохранено.')
                current=HousingEvent.objects.select_for_update().filter(company=request.user.company,person=person,kind='DEPARTURE',stay=None).order_by('-created_at','-pk').first()
                if (current.updated_at.isoformat() if current else '')!=form.cleaned_data['version']:raise ValidationError('Выезд уже уточнил другой пользователь. Обновите форму.')
                row=current or HousingEvent(company=request.user.company,person=person,kind='DEPARTURE',departure_destination='REST',recorded_by=request.user)
                row.date=form.cleaned_data['date'];row.from_camp=form.cleaned_data['camp'];row.comment=form.cleaned_data['comment'];row.save()
            messages.success(request,'Фактический выезд сохранён. МО и график считаются от этой даты.')
            return redirect('personnel:housing_history',pk=person.pk)
        except ValidationError as exc:form.add_error(None,'; '.join(exc.messages))
    return render(request,'personnel/initial_departure.html',{'person':person,'form':form})
