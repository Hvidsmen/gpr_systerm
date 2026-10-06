from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.contrib import messages
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _
from datetime import timedelta
from .models import Project, ConstructionObject, Section
from .forms import ProjectForm, ConstructionObjectForm, SectionForm


from core.mixins import CompanyScopedMixin

class ProjectListView(CompanyScopedMixin, ListView):
    model = Project
    template_name = 'projects/project_list.html'
    context_object_name = 'projects'
    paginate_by = 20


class ProjectDetailView(CompanyScopedMixin, DetailView):
    model = Project
    template_name = 'projects/project_detail.html'
    context_object_name = 'project'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['construction_objects'] = self.object.construction_objects.all()
        return context


class ProjectCreateView(CompanyScopedMixin, CreateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        form.instance.company = self.get_company()
        messages.success(self.request, _('Проект успешно создан!'))
        return super().form_valid(form)


class ProjectUpdateView(CompanyScopedMixin, UpdateView):
    model = Project
    form_class = ProjectForm
    template_name = 'projects/project_form.html'
    success_url = reverse_lazy('projects:project_list')

    def form_valid(self, form):
        messages.success(self.request, _('Проект успешно обновлен!'))
        return super().form_valid(form)


class ProjectDeleteView(CompanyScopedMixin, DeleteView):
    model = Project
    template_name = 'projects/project_confirm_delete.html'
    success_url = reverse_lazy('projects:project_list')

    def delete(self, request, *args, **kwargs):
        messages.success(request, _('Проект успешно удален!'))
        return super().delete(request, *args, **kwargs)


class ConstructionObjectListView(CompanyScopedMixin, ListView):
    template_name = 'projects/object_list.html'
    context_object_name = 'construction_objects'
    paginate_by = 20

    def get_queryset(self):
        self.project = get_object_or_404(Project, company=self.request.user.company, pk=self.kwargs['project_pk'])
        return ConstructionObject.objects.filter(company=self.request.user.company, project=self.project)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = self.project
        return context


class ConstructionObjectCreateView(CompanyScopedMixin, CreateView):
    model = ConstructionObject
    form_class = ConstructionObjectForm
    template_name = 'projects/object_form.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project'] = get_object_or_404(Project, company=self.request.user.company, pk=self.kwargs['project_pk'])
        return context

    def form_valid(self, form):
        project = get_object_or_404(Project, company=self.request.user.company, pk=self.kwargs['project_pk'])
        form.instance.project = project
        form.instance.company = project.company
        messages.success(self.request, _('Объект успешно создан!'))
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy('projects:object_list', kwargs={'project_pk': self.kwargs['project_pk']})


from django.views.generic import UpdateView, CreateView, DeleteView
from django.contrib import messages
from django.urls import reverse_lazy
from django.shortcuts import get_object_or_404

from .models import Section, ConstructionObject
from .forms import SectionForm


class SectionListView(CompanyScopedMixin, ListView):
    """Список разделов строительного объекта."""
    model = Section
    template_name = 'projects/section_list.html'
    context_object_name = 'sections'

    def dispatch(self, request, *args, **kwargs):
        object_id = kwargs.get('pk') or kwargs.get('object_id')
        if not object_id:
            raise Http404("ID объекта не указан")

        self.construction_object = get_object_or_404(
            ConstructionObject,
            pk=object_id,
            company=request.user.company
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return Section.objects.filter(
            company=self.request.user.company,
            construction_object=self.construction_object
        ).order_by('code')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['construction_object'] = self.construction_object
        context['project_pk'] = self.construction_object.project_id  # ← ДОБАВЛЕНО
        return context

class SectionCreateView(CompanyScopedMixin, CreateView):
    model = Section
    form_class = SectionForm
    template_name = 'projects/section_form.html'

    def dispatch(self, request, *args, **kwargs):
        object_id = kwargs.get('pk') or kwargs.get('object_id')
        self.construction_object = get_object_or_404(
            ConstructionObject, pk=object_id, company=request.user.company
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['construction_object'] = self.construction_object
        context['project_pk'] = self.construction_object.project_id  # ← ДОБАВЛЕНО
        context['title'] = 'Новый раздел'
        return context


class SectionUpdateView(CompanyScopedMixin, UpdateView):
    """Редактирование раздела."""
    model = Section
    pk_url_kwarg = 'section_pk'
    form_class = SectionForm
    template_name = 'projects/section_form.html'

    def get_object(self, queryset=None):
        """Получаем раздел, проверяя принадлежность объекту."""
        section = super().get_object(queryset)
        # Проверяем, что раздел принадлежит нужному объекту
        object_id = self.kwargs.get('pk') or self.kwargs.get('object_id')
        if section.construction_object_id != int(object_id):
            raise Http404("Раздел не принадлежит этому объекту")
        return section

    def form_valid(self, form):
        messages.success(self.request, f'Раздел "{form.instance.name}" обновлён!')
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy(
            'projects:section_list',
            kwargs={'pk': self.object.construction_object_id}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['construction_object'] = self.object.construction_object
        context['project_pk'] = self.object.construction_object.project_id  # ← ДОБАВЛЕНО
        context['title'] = f'Редактирование раздела "{self.object.name}"'
        return context


class SectionDeleteView(CompanyScopedMixin, DeleteView):
    """Удаление раздела."""
    model = Section
    pk_url_kwarg = 'section_pk'
    template_name = 'projects/section_confirm_delete.html'

    def get_object(self, queryset=None):
        section = super().get_object(queryset)
        object_id = self.kwargs.get('pk') or self.kwargs.get('object_id')
        if section.construction_object_id != int(object_id):
            raise Http404("Раздел не принадлежит этому объекту")
        return section

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['project_pk'] = self.object.construction_object.project_id  # ← ДОБАВЛЕНО
        return context

    def get_success_url(self):
        return reverse_lazy(
            'projects:section_list',
            kwargs={'pk': self.object.construction_object_id}
        )

    def delete(self, request, *args, **kwargs):
        section = self.get_object()
        name = section.name
        messages.success(request, f'Раздел "{name}" удалён!')
        return super().delete(request, *args, **kwargs)
