from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from core.models import OpenFactPeriod


class OpenFactPeriodForm(forms.ModelForm):
    class Meta:
        model=OpenFactPeriod
        fields=['name','date_from','date_to']
        widgets={'date_from':forms.DateInput(attrs={'type':'date'}),'date_to':forms.DateInput(attrs={'type':'date'})}

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():field.widget.attrs.setdefault('class','form-control')

    def clean(self):
        data=super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from']>data['date_to']:
            raise ValidationError('Дата окончания не может быть раньше даты начала.')
        return data


def periods(request):
    rows=OpenFactPeriod.objects.filter(company=request.user.company)
    form=OpenFactPeriodForm(request.POST or None)
    if request.method=='POST':
        if request.POST.get('action')=='delete':
            row=get_object_or_404(rows,pk=request.POST.get('period'))
            row.delete();messages.success(request,'Открытый период удалён.')
            return redirect(reverse('accounts:fact_open_periods'))
        if form.is_valid():
            row=form.save(commit=False);row.company=request.user.company;row.save()
            messages.success(request,'Период открыт для внесения факта.')
            return redirect(reverse('accounts:fact_open_periods'))
    return render(request,'accounts/fact_open_periods.html',{'form':form,'periods':rows})
