from importlib import import_module
from types import SimpleNamespace
from django.apps import apps
from django.test import TestCase
from django.core.exceptions import ValidationError
from apps.projects.models import Section
from . import test_schedule_import as fixtures
from .schedule_import import apply_rows,inspect_rows
from .models import ProjectWork

class ComplexObjectSectionTests(TestCase):
    def setUp(self):
        fixtures.ScheduleImportTests.setUp(self)
        apply_rows(self.user,self.section,self.profile,self.rows)
        self.work=ProjectWork.objects.get(section=self.section)
        self.facility=self.work.complex_object
        self.other=Section.objects.create(company=self.company,construction_object=self.obj,name='Другой раздел')

    def test_work_and_import_reject_other_section(self):
        self.assertEqual(self.facility.section_id,self.section.pk)
        self.work.section=self.other
        with self.assertRaises(ValidationError):self.work.full_clean()
        with self.assertRaises(ValidationError):self.work.save()
        with self.assertRaises(ValidationError):inspect_rows(self.company,self.other,self.rows)
        self.work.refresh_from_db()
        self.assertEqual(self.work.section_id,self.section.pk)

    def test_reassigning_object_moves_all_work_together(self):
        self.facility.section=self.other
        self.facility.save()
        self.facility.refresh_from_db();self.work.refresh_from_db()
        self.assertEqual(self.facility.section_id,self.other.pk)
        self.assertEqual(self.work.section_id,self.other.pk)

    def test_migration_groups_old_split_object_into_majority_section(self):
        ProjectWork.objects.bulk_create([
            ProjectWork(company=self.company,section=self.other,complex_object=self.facility,kind='COMPOSITE',code='OLD-1',name='Вторая'),
            ProjectWork(company=self.company,section=self.other,complex_object=self.facility,kind='COMPOSITE',code='OLD-2',name='Третья'),
        ])
        ids=set(ProjectWork.objects.values_list('pk',flat=True))
        import_module('apps.works.migrations.0015_complex_object_section').backfill(apps,SimpleNamespace(connection=SimpleNamespace(alias='default')))
        self.facility.refresh_from_db()
        self.assertEqual(self.facility.section_id,self.other.pk)
        self.assertEqual(set(self.facility.work_packages.values_list('section_id',flat=True)),{self.other.pk})
        self.assertEqual(set(ProjectWork.objects.values_list('pk',flat=True)),ids)
        self.assertEqual(self.work.items.count(),2)

    def test_move_preserves_items_and_fact_and_rolls_back_on_error(self):
        from datetime import date
        from unittest.mock import patch
        from apps.production.models import DailyFact
        fact=DailyFact.objects.create(company=self.company,project_work=self.work,work_item=self.work.items.first(),date=date(2026,10,6),actual_quantity=2)
        item_ids=set(self.work.items.values_list('pk',flat=True))
        self.facility.section=self.other
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=ValidationError('Ошибка аудита')):
            with self.assertRaises(ValidationError):self.facility.save()
        self.facility.refresh_from_db();self.work.refresh_from_db()
        self.assertEqual(self.work.section_id,self.section.pk)
        self.assertEqual(self.facility.section_id,self.section.pk)
        self.facility.section=self.other;self.facility.save()
        self.work.refresh_from_db()
        self.assertEqual(self.work.section_id,self.other.pk)
        self.assertEqual(set(self.work.items.values_list('pk',flat=True)),item_ids)
        self.assertTrue(DailyFact.objects.filter(pk=fact.pk,project_work_id=self.work.pk).exists())

    def test_conflicting_work_code_cancels_entire_move(self):
        ProjectWork.objects.create(company=self.company,section=self.other,code=self.work.code,name='Конфликт',kind='COMPOSITE')
        self.facility.section=self.other
        with self.assertRaisesMessage(ValidationError,'совпадающими кодами'):self.facility.save()
        self.work.refresh_from_db();self.facility.refresh_from_db()
        self.assertEqual(self.work.section_id,self.section.pk)
        self.assertEqual(self.facility.section_id,self.section.pk)
