from django.test import TestCase
from django.urls import reverse
from .models import Company, Role, User, OneCConnection, OneCDiagnosticRun


class OneCJobTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Jobs company')
        role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name':'Admin'})
        self.admin = User.objects.create_user(username='jobs-admin', company=self.company, role=role)
        self.client.force_login(self.admin)
        self.connection = OneCConnection.objects.create(company=self.company)
        self.url = reverse('accounts:one_c_jobs')

    def run_record(self, **kwargs):
        return OneCDiagnosticRun.objects.create(connection=self.connection, server='old-server', database='old-database', **kwargs)

    def test_all_types_old_connections_and_result(self):
        self.run_record(report={}, status='QUEUED')
        self.run_record(report={'metadata_only':True}, status='ERROR', error='Metadata failed')
        self.run_record(report={'positions_import':True,'positions_result':{'created':3,'existing':2,'duplicates':0,'empty':0}},status='DONE')
        response = self.client.get(self.url)
        self.assertEqual(response.context['page'].paginator.count,3)
        for label in ['Диагностика структуры','Чтение названий','Загрузка должностей','Metadata failed','Добавлено: 3','old-database']:
            self.assertContains(response,label)
        self.assertTrue(response.context['pending'])

    def test_filters_pagination_and_isolation(self):
        self.run_record(report={},status='DONE')
        self.run_record(report={'metadata_only':True},status='DONE')
        self.run_record(report={'positions_import':True},status='ERROR')
        for kind in ['diagnostic','metadata','positions']:
            self.assertEqual(self.client.get(self.url, {'kind':kind}).context['page'].paginator.count,1)
        other = OneCConnection.objects.create(company=Company.objects.create(name='Other jobs'))
        OneCDiagnosticRun.objects.create(connection=other,server='hidden-server',database='secret-db')
        self.assertNotContains(self.client.get(self.url),'secret-db')
        self.assertEqual(self.client.get(self.url, {'status':'ERROR','kind':'positions'}).context['page'].paginator.count,1)
        OneCDiagnosticRun.objects.bulk_create([OneCDiagnosticRun(connection=self.connection,server='server',database='db') for _ in range(51)])
        page = self.client.get(self.url, {'page':2}).context['page']
        self.assertEqual(page.number,2)
        self.assertEqual(page.paginator.count,54)

    def test_non_admin_denied(self):
        role,_=Role.objects.get_or_create(code='PLANNER',defaults={'name':'Planner'})
        user=User.objects.create_user(username='jobs-planner',company=self.company,role=role)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code,403)
        self.assertEqual(self.client.post(self.url).status_code,403)

    def test_old_empty_import_is_not_displayed_as_successful_load(self):
        self.run_record(report={'positions_import':True,'positions_result':{'created':0,'existing':0,'duplicates':0,'empty':0}},status='DONE')
        response=self.client.get(self.url)
        self.assertContains(response,'Данные не загружены')
        self.assertContains(response,reverse('resources:brigade_list'))
