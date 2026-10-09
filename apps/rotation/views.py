import calendar
from urllib.parse import urlencode
from django.urls import reverse
from datetime import date
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from core.permissions import PLAN_ROLES, require_roles
from .models import RotationPlan, RotationRole, RotationPerson
from .forms import PlanForm, RoleForm, PersonForm
from .services import refresh_demand, generate_people, matrix


class PlanList(View):
    def get(self, request):
        plans = RotationPlan.objects.filter(company=request.user.company).select_related('source__construction_object')
        return render(request, 'rotation/list.html', {'plans': plans})


class PlanCreate(View):
    def get(self, request):
        return render(request, 'rotation/form.html', {'form': PlanForm(user=request.user), 'title':'Новая перевахта'})

    def post(self, request):
        form = PlanForm(request.POST, user=request.user)
        if form.is_valid():
            try:
                with transaction.atomic():
                    plan = form.save()
                    refresh_demand(plan)
                messages.success(request, 'Перевахта создана. Работники сформированы автоматически по потребности и графикам должностей.')
                return redirect('rotation:plan_detail', pk=plan.pk)
            except ValidationError as error:
                form.add_error(None, error)
        return render(request, 'rotation/form.html', {'form': form, 'title':'Новая перевахта'})


class PlanDetail(View):
    def get(self, request, pk):
        plan = get_object_or_404(RotationPlan, pk=pk, company=request.user.company)
        try:
            chosen = date.fromisoformat(request.GET.get('month', plan.start.strftime('%Y-%m')+'-01') + ('-01' if len(request.GET.get('month', '')) == 7 else ''))
        except ValueError:
            chosen = plan.start
        start = max(plan.start, chosen.replace(day=1))
        end = min(plan.end, chosen.replace(day=calendar.monthrange(chosen.year, chosen.month)[1]))
        if start > end:
            start = plan.start
            end = min(plan.end, start.replace(day=calendar.monthrange(start.year, start.month)[1]))
        days, rows = matrix(plan, start, end)
        return render(request, 'rotation/detail.html', {'plan':plan, 'days':days, 'rows':rows, 'month':start.strftime('%Y-%m')})

    def post(self, request, pk):
        require_roles(request.user, PLAN_ROLES)
        plan = get_object_or_404(RotationPlan, pk=pk, company=request.user.company)
        action = request.POST.get('action')
        try:
            if action == 'refresh':
                refresh_demand(plan)
                messages.success(request, 'Потребность обновлена. Личные графики сохранены.')
            elif action in {'generate', 'apply'}:
                try:
                    position_id = int(request.POST.get('position', ''))
                except (ValueError, TypeError):
                    raise ValidationError('Выберите должность.')
                position = get_object_or_404(RotationRole, pk=position_id, plan=plan, company=request.user.company)
                if action == 'generate':
                    added = generate_people(position)
                    messages.success(request, f'Добавлено мест: {added}. Существующие графики сохранены; проверьте нехватку в матрице.')
                else:
                    position.people.update(on_days=position.on_days, off_days=position.off_days)
                    messages.success(request, 'Длительность вахты и отдыха применена к людям. Их даты начала сохранены.')
            else:
                messages.error(request, 'Неизвестное действие.')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
        return redirect(reverse('rotation:plan_detail', args=[pk]) + '?' + urlencode({'month': request.POST.get('month', '')}))


class RoleEdit(View):
    def dispatch(self, request, pk, *args, **kwargs):
        self.position = get_object_or_404(RotationRole, pk=pk, company=request.user.company)
        return super().dispatch(request, pk, *args, **kwargs)

    def get(self, request, pk):
        return self.display(request, RoleForm(instance=self.position))

    def post(self, request, pk):
        form = RoleForm(request.POST, instance=self.position)
        if form.is_valid():
            form.save()
            messages.success(request, 'График должности изменён. Личные графики не изменены.')
            return redirect('rotation:plan_detail', pk=self.position.plan_id)
        return self.display(request, form)

    def display(self, request, form):
        return render(request, 'rotation/form.html', {'form':form, 'title':f'График должности: {self.position}', 'plan':self.position.plan})


class PersonEdit(View):
    def dispatch(self, request, pk, *args, **kwargs):
        self.person = get_object_or_404(RotationPerson, pk=pk, company=request.user.company) if request.resolver_match.url_name == 'person_update' else None
        self.position = self.person.position if self.person else get_object_or_404(RotationRole, pk=pk, company=request.user.company)
        return super().dispatch(request, pk, *args, **kwargs)

    def get(self, request, pk):
        initial = {'name':f'Работник {self.position.people.count()+1}', 'on_days':self.position.on_days, 'off_days':self.position.off_days, 'anchor':self.position.anchor}
        return self.display(request, PersonForm(instance=self.person, initial=initial if not self.person else None))

    def post(self, request, pk):
        person = self.person or RotationPerson(company=request.user.company, position=self.position)
        form = PersonForm(request.POST, instance=person)
        if form.is_valid():
            form.save()
            return redirect('rotation:plan_detail', pk=self.position.plan_id)
        return self.display(request, form)

    def display(self, request, form):
        return render(request, 'rotation/form.html', {'form':form, 'title':f'Личный график · {self.position}', 'plan':self.position.plan})


class PersonDelete(View):
    def get(self, request, pk):
        person = get_object_or_404(RotationPerson, pk=pk, company=request.user.company)
        return render(request, 'rotation/delete.html', {'person':person})

    def post(self, request, pk):
        person = get_object_or_404(RotationPerson, pk=pk, company=request.user.company)
        plan_id = person.position.plan_id
        person.delete()
        return redirect('rotation:plan_detail', pk=plan_id)


class StatusEdit(View):
    def dispatch(self, request, pk, *args, **kwargs):
        self.person = get_object_or_404(RotationPerson.objects.select_related('position__plan'), pk=pk, company=request.user.company)
        return super().dispatch(request, pk, *args, **kwargs)

    def get(self, request, pk):
        from .forms import StatusForm
        from .models import RotationStatus
        try:
            day = date.fromisoformat(request.GET.get('date', self.person.position.plan.start.isoformat()))
        except ValueError:
            from django.http import HttpResponseBadRequest
            return HttpResponseBadRequest('Некорректная дата')
        entry = RotationStatus.objects.filter(person=self.person, day=day).first()
        form = StatusForm(person=self.person, initial={'day':day, 'status':entry.status if entry else 'AUTO'})
        return self.display(request, form)

    def post(self, request, pk):
        from .forms import StatusForm
        from .models import RotationStatus
        form = StatusForm(request.POST, person=self.person)
        if form.is_valid():
            day, status = form.cleaned_data['day'], form.cleaned_data['status']
            if status == 'AUTO':
                RotationStatus.objects.filter(person=self.person, day=day, company=request.user.company).delete()
            else:
                RotationStatus.objects.update_or_create(person=self.person, day=day, company=request.user.company, defaults={'status':status})
            messages.success(request, 'Статус обновлён. Численность и нехватка пересчитаны.')
            return redirect(reverse('rotation:plan_detail', args=[self.person.position.plan_id]) + '?' + urlencode({'month':day.strftime('%Y-%m')}))
        return self.display(request, form)

    def display(self, request, form):
        return render(request, 'rotation/form.html', {'form':form, 'title':f'Статус перевахты · {self.person.name}', 'plan':self.person.position.plan})


class PlanExport(View):
    def get(self, request, pk):
        from django.http import HttpResponse, HttpResponseBadRequest
        from .excel import export_matrix
        plan = get_object_or_404(RotationPlan, pk=pk, company=request.user.company)
        start, end = plan.start, plan.end
        month = request.GET.get('month')
        if month:
            try:
                chosen = date.fromisoformat(month+'-01')
                start = max(start, chosen)
                end = min(end, chosen.replace(day=calendar.monthrange(chosen.year, chosen.month)[1]))
                if start > end: raise ValueError()
            except ValueError:
                return HttpResponseBadRequest('Некорректный месяц')
        response = HttpResponse(export_matrix(plan,start,end), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="rotation-{plan.pk}-{month or "all"}.xlsx"'
        return response


class StatusReset(View):
    """Preview exact scope; deleting overrides requires an explicit POST confirmation."""
    def dispatch(self, request, scope, pk, *args, **kwargs):
        from .models import RotationStatus
        from apps.projects.models import ConstructionObject
        require_roles(request.user, PLAN_ROLES)
        self.plan = None
        self.scope = scope
        self.statuses = RotationStatus.objects.filter(company=request.user.company)
        if scope == 'all' and pk == 0:
            self.label = 'Все планы перевахты вашей компании'
        elif scope == 'plan':
            self.plan = get_object_or_404(RotationPlan, pk=pk, company=request.user.company)
            self.label = f'План перевахты: {self.plan.title}'
            self.statuses = self.statuses.filter(person__position__plan=self.plan)
        elif scope == 'role':
            position = get_object_or_404(RotationRole.objects.select_related('plan', 'brigade'), pk=pk, company=request.user.company)
            self.plan = position.plan
            self.label = f'Должность: {position.brigade} · {position.plan.title}'
            self.statuses = self.statuses.filter(person__position=position)
        elif scope == 'object':
            obj = get_object_or_404(ConstructionObject, pk=pk, company=request.user.company)
            self.label = f'Строительный объект: {obj}'
            self.statuses = self.statuses.filter(person__position__plan__source__construction_object=obj)
        else:
            from django.http import Http404
            raise Http404()
        return super().dispatch(request, scope, pk, *args, **kwargs)

    def get(self, request, scope, pk):
        from .forms import ResetForm
        return self.display(request, ResetForm())

    def post(self, request, scope, pk):
        from .forms import ResetForm
        form = ResetForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                deleted, _ = self.statuses.delete()
            messages.success(request, f'Сброшено ручных статусов: {deleted}. Расчёт выполняется по сохранённым личным графикам.')
            return redirect(self.back_url(request))
        return self.display(request, form)

    def back_url(self, request):
        if self.plan:
            return reverse('rotation:plan_detail', args=[self.plan.pk])+'?'+urlencode({'month':request.POST.get('month',request.GET.get('month',''))})
        return reverse('rotation:plan_list')

    def display(self, request, form):
        return render(request, 'rotation/reset.html', {'form':form, 'label':self.label, 'scope':self.scope,
            'count':self.statuses.count(), 'plan_count':self.statuses.values('person__position__plan_id').distinct().count(),
            'back_url':self.back_url(request), 'month':request.POST.get('month',request.GET.get('month',''))})
