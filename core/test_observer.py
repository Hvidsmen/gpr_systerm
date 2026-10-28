from types import SimpleNamespace
from django.test import TestCase
from django.core.exceptions import PermissionDenied
from core.permissions import check_route
from apps.accounts.models import Company, Role, User
from apps.accounts.forms import CompanyUserAccessForm


class ObserverPermissionTests(TestCase):
    def setUp(self):
        self.role = Role.objects.get(code='OBSERVER')
        self.user = User.objects.create_user(username='observer', company=Company.objects.create(name='Observer company'), role=self.role)

    def test_can_view_business_sections_and_exports(self):
        for namespace, name in [('dashboard','index'), ('dashboard','revenue'), ('planning','workspace_list'),('planning','workspace_edit'), ('planning','global_detail'), ('planning','journal_plan_list'), ('planning','report_matrix'), ('rotation','plan_detail'), ('production','fact_day_workspace'), ('projects','project_list'), ('works','work_list'), ('resources','brigade_list'), ('planning','workspace_excel_export')]:
            check_route(self.user, SimpleNamespace(namespace=namespace, url_name=name, kwargs={}), 'GET')

    def test_all_write_methods_and_mutation_pages_are_denied(self):
        for namespace, name, method in [('planning','global_action','POST'), ('production','fact_day_workspace','POST'), ('works','work_list','POST'), ('resources','brigade_update','GET'), ('projects','project_create','GET'), ('accounts','user_access','POST'), ('accounts','settings','POST')]:
            with self.assertRaises(PermissionDenied):
                check_route(self.user, SimpleNamespace(namespace=namespace, url_name=name, kwargs={}), method)

    def test_admin_can_assign_observer(self):
        form = CompanyUserAccessForm(company=self.user.company)
        self.assertTrue(form.fields['role'].queryset.filter(code='OBSERVER').exists())

    def test_http_post_cannot_create_fact(self):
        from django.urls import reverse
        from apps.production.models import LaborFact
        self.client.force_login(self.user)
        response = self.client.post(reverse('production:fact_day_workspace'), {})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(LaborFact.objects.exists())
