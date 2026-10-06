# core/mixins.py
from apps.accounts.models import Company


class CompanyRequiredMixin:
    """
    Миксин для автоматической привязки компании к создаваемому объекту.
    """

    def get_company(self):
        user = self.request.user
        if user.is_authenticated and user.company:
            return user.company
        company, created = Company.objects.get_or_create(
            name=f'Компания пользователя {user.username if user.is_authenticated else "anon"}',
            defaults={'inn': '0000000000'}
        )
        if user.is_authenticated and not user.company:
            user.company = company
            user.save(update_fields=['company'])
        return company

    def form_valid(self, form):
        # form.instance существует только в CreateView/UpdateView
        # В DeleteView формы нет — пропускаем
        if hasattr(form, 'instance'):
            form.instance.company = self.get_company()
        return super().form_valid(form)

    def get_queryset(self):
        """
        Ограничиваем queryset только объектами компании пользователя.
        Работает для ListView, DetailView, UpdateView, DeleteView.
        """
        qs = super().get_queryset()
        return qs.filter(company=self.request.user.company)