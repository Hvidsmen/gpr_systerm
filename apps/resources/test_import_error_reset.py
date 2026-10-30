from django.core.exceptions import ValidationError
from django.urls import reverse
from django.test import TestCase
from unittest.mock import patch
from .test_transport_import import TransportImportTests,upload
from . import transport_import as service
from .models import Equipment
from .transport_import_models import TransportImportRow
from apps.production.transport_models import EquipmentDayRecord
from apps.production.history_models import FactRevision

class ImportResetTests(TestCase):
    setUpTestData=classmethod(TransportImportTests.setUpTestData.__func__)
    row=TransportImportTests.row
    def batch(self,name='Сводка.xlsm',bad=True):
        return service.preview(self.admin,[upload([self.row(year='190' if bad else '2021')],name)],self.day)
    def test_reupload_replaces_only_this_file_after_apply_and_is_transactional(self):
        first=self.batch();service.apply(self.admin,first)
        unrelated=self.batch('Другой файл.xlsm');service.apply(self.admin,unrelated)
        again=self.batch();self.assertEqual(first.rows.get().status,'PENDING')
        service.apply(self.admin,again)
        self.assertEqual(first.rows.get().status,'SUPERSEDED');self.assertEqual(unrelated.rows.get().status,'PENDING');self.assertEqual(again.rows.get().status,'PENDING')
        corrected=self.batch(bad=False);service.apply(self.admin,corrected)
        self.assertEqual(again.rows.get().status,'SUPERSEDED');self.assertEqual(corrected.rows.get().status,'APPLIED')
    def test_stale_preview_does_not_clear_history_and_reset_does_not_touch_facts(self):
        first=self.batch();service.apply(self.admin,first)
        again=self.batch();self.machine.name='Changed';self.machine.save()
        with self.assertRaises(ValidationError):service.apply(self.admin,again)
        self.assertEqual(first.rows.get().status,'PENDING')
        good=self.batch('Good.xlsm',bad=False);service.apply(self.admin,good)
        before=(Equipment.objects.count(),EquipmentDayRecord.objects.count())
        self.client.force_login(self.operator);url=reverse('resources:transport_import_errors')
        self.assertContains(self.client.get(url),'Сбросить историю ошибок')
        self.assertEqual(self.client.post(url,{'action':'clear_history'}).status_code,302)
        self.assertEqual(first.rows.get().status,'CLEARED');self.assertEqual(good.rows.get().status,'APPLIED')
        self.assertEqual((Equipment.objects.count(),EquipmentDayRecord.objects.count()),before)
        self.assertEqual(again.rows.get().status,'PENDING') # unapplied preview retained
        self.assertTrue(FactRevision.objects.filter(entity_id=str(first.rows.get().pk),actor=self.operator).exists())
