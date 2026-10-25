import json
from django.test import TestCase
from django.urls import reverse
from .models import Company, Role, User, OneCConnection, OneCDiagnosticRun


class StructureReportTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name='Report company')
        role, _ = Role.objects.get_or_create(code='ADMIN', defaults={'name': 'Admin'})
        self.user = User.objects.create_user(username='report-admin', company=self.company, role=role)
        self.client.force_login(self.user)
        self.connection = OneCConnection.objects.create(company=self.company, server='host', database='zup')
        self.url = reverse('accounts:one_c_report')

    def run_report(self, **kwargs):
        return OneCDiagnosticRun.objects.create(connection=self.connection, server='host', database='zup', status='DONE', **kwargs)

    def report(self):
        return {'schema': [
            {'schema': 'dbo', 'table': '_Reference1', 'column': '_Code', 'type': 'nvarchar', 'max_length': 40, 'is_nullable': False},
            {'schema': 'dbo', 'table': '_Reference1', 'column': '_Fld3RRef', 'type': 'binary'},
            {'schema': 'dbo', 'table': '_Reference2', 'column': '_Description', 'type': 'nvarchar'},
            {'schema': 'dbo', 'table': '_Reference4', 'column': '_Fld5', 'type': 'datetime'}],
            'metadata_labels': {'dbo._Reference1': {'name': 'Справочник.Сотрудники', 'columns': {'_Code': 'ТабельныйНомер'},
                'references': {'_Fld3RRef': ['dbo._Reference2']}}, 'dbo._Reference2': {'name': 'Справочник.ФизическиеЛица'}},
            'candidates': [{'secret_employee_data': 'PRIVATE_SAMPLE'}], 'windows_identity': 'PRIVATE_LOGIN',
            'connection': {'login': 'PRIVATE_LOGIN'}, 'metadata_status': 'decoded'}

    def test_complete_download_names_references_and_no_record_values(self):
        run = self.run_report(report=self.report())
        # The export must include all tables regardless of catalogue search/pagination.
        response = self.client.get(self.url, {'q': 'nothing', 'page': '99'})
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['diagnostic']['id'], str(run.pk))
        self.assertEqual(data['summary']['tables'], 3)
        self.assertEqual(data['summary']['tables_without_names'], 1)
        employee = next(t for t in data['tables'] if t['table'] == '_Reference1')
        self.assertEqual(employee['columns'][0]['name'], 'ТабельныйНомер')
        self.assertEqual(employee['columns'][1]['targets'][0]['name'], 'Справочник.ФизическиеЛица')
        self.assertNotIn(b'PRIVATE_', response.content)
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertIn('no-store', response['Cache-Control'])
        self.assertContains(self.client.get(reverse('accounts:one_c')), 'Скачать отчёт структуры (JSON)')

    def test_manual_names_only_for_same_database(self):
        self.run_report(report=self.report())
        self.connection.metadata_server = 'host'; self.connection.metadata_database = 'zup'
        self.connection.metadata_labels = {'dbo._Reference1': {'name': 'Уточнение', 'columns': {'_Fld3RRef': 'ФизическоеЛицо'}}}
        self.connection.save()
        tables = self.client.get(self.url).json()['tables']
        employee = next(t for t in tables if t['table'] == '_Reference1')
        self.assertEqual(employee['name'], 'Уточнение')
        self.assertEqual(employee['columns'][0]['name'], 'ТабельныйНомер')
        self.assertEqual(employee['columns'][1]['name'], 'ФизическоеЛицо')
        self.connection.metadata_database = 'old'; self.connection.save()
        self.assertNotContains(self.client.get(self.url), 'Уточнение')

    def test_access_missing_structure_and_connection_scope(self):
        self.assertEqual(self.client.get(self.url).status_code, 404)
        other = OneCConnection.objects.create(company=Company.objects.create(name='Other'))
        OneCDiagnosticRun.objects.create(connection=other, server='host', database='zup', status='DONE', report=self.report())
        self.assertEqual(self.client.get(self.url).status_code, 404)
        run = self.run_report(report=self.report())
        self.connection.database = 'changed'; self.connection.save()
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.connection.database = 'zup'; self.connection.save()
        # Newer failed/import jobs cannot replace a successful structure report.
        self.run_report(report={'positions_import': True})
        OneCDiagnosticRun.objects.create(connection=self.connection, server='host', database='zup', status='ERROR', report={})
        self.assertEqual(self.client.get(self.url).json()['diagnostic']['id'], str(run.pk))
        self.assertEqual(self.client.post(self.url).status_code, 405)
        role, _ = Role.objects.get_or_create(code='PLANNER', defaults={'name': 'Planner'})
        self.user.role = role; self.user.save()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
