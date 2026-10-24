from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from .test_one_c import OneCTests
from .models import OneCDiagnosticRun
from .one_c_positions import apply_names
from apps.resources.models import Brigade, PositionPlanningBrigade


class PositionImportTests(TestCase):
    def setUp(self):
        OneCTests.setUp(self)

    def run_record(self):
        return OneCDiagnosticRun.objects.create(connection=self.connection, created_by=self.admin, server=self.connection.server, database=self.connection.database)

    def test_names_only_dedup_repeat_and_shared_position(self):
        run = self.run_record()
        data = {'position_names': [{'name': '  Водитель '}, {'name': 'водитель'}, {'name': ''}]}
        result = apply_names(run, data)
        self.assertEqual(result, {'created': 1, 'existing': 0, 'duplicates': 1, 'empty': 1})
        self.assertEqual(apply_names(run, data)['created'], 0)
        brigade = Brigade.objects.get(company=self.company)
        self.assertEqual(brigade.name, 'Водитель')
        self.assertTrue(PositionPlanningBrigade.objects.filter(brigade=brigade).exists())

    def test_invalid_name_rolls_back(self):
        with self.assertRaises(ValidationError):
            apply_names(self.run_record(), {'position_names': [{'name': 'Новая'}, {'name': 'x'*151}]})
        self.assertFalse(Brigade.objects.filter(company=self.company).exists())

    def test_existing_fields_and_other_company_untouched(self):
        existing = Brigade.objects.create(company=self.company, name='Повар', description='Сохранить', is_active=False)
        result = apply_names(self.run_record(), {'position_names': [{'name': 'повар'}]})
        existing.refresh_from_db()
        self.assertEqual(result['created'], 0)
        self.assertFalse(existing.is_active)
        self.assertEqual(existing.description, 'Сохранить')

    def test_table_queues_only_confirmed_source_and_keeps_diagnostic(self):
        report = {'schema': [{'schema':'dbo','table':'_Reference92','column': col} for col in ['_Description','_Marked','_Fld25711']], 'metadata_labels': {'dbo._Reference92': {'name':'Справочник.Должности','columns':{'_Fld25711':'ИсключенаИзШтатногоРасписания'}}}}
        diagnostic = self.run_record()
        diagnostic.status = 'DONE'; diagnostic.report = report; diagnostic.save()
        url = reverse('accounts:one_c_table', args=['dbo','_Reference92'])
        self.assertContains(self.client.get(url), 'Загрузить актуальные должности')
        self.assertEqual(self.client.post(url, {'action':'import_positions'}).status_code,302)
        queued = OneCDiagnosticRun.objects.get(status='QUEUED')
        self.assertTrue(queued.report['positions_import'])
        queued.status='DONE';queued.save()
        self.assertContains(self.client.get(url), 'Загрузить актуальные должности')

    def test_empty_source_is_an_explicit_error_with_filter_counts(self):
        run=self.run_record()
        with self.assertRaisesMessage(ValidationError,'Всего в dbo._Reference92: 100'):
            apply_names(run,{'position_names':[],'position_source':{'total':100,'not_marked':90,'excluded':90,'eligible':0}})
        self.assertFalse(Brigade.objects.filter(company=self.company).exists())

    def test_only_blank_names_cannot_report_success(self):
        with self.assertRaisesMessage(ValidationError,'все полученные наименования пустые'):
            apply_names(self.run_record(),{'position_names':[{'name':'   '}]})
