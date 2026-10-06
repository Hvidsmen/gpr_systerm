from django import forms
from django.core.exceptions import PermissionDenied


class CompanyScopedMixin:
    """Scope generic-view objects and related form choices to the current company."""

    def get_company(self):
        user = self.request.user
        if not user.is_authenticated or not user.company_id:
            raise PermissionDenied('Пользователю не назначена компания.')
        return user.company

    def get_queryset(self):
        return super().get_queryset().filter(company=self.get_company())

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        company = self.get_company()
        if hasattr(form, "instance") and hasattr(form.instance, "company_id"):
            form.instance.company = company
        for field in form.fields.values():
            if isinstance(field, (forms.ModelChoiceField, forms.ModelMultipleChoiceField)):
                model = field.queryset.model
                if any(f.name == 'company' for f in model._meta.fields):
                    field.queryset = field.queryset.filter(company=company)
        return form


class CompanyRequiredMixin(CompanyScopedMixin):
    """Assign the authenticated user's company; never create one during a request."""

    def form_valid(self, form):
        if hasattr(form, 'instance'):
            form.instance.company = self.get_company()
        return super().form_valid(form)
