from django.test import TestCase
from .test_transport_import import TransportImportTests,upload
from . import transport_import as service
from .models import Equipment
from apps.production.transport_models import EquipmentDayRecord
from apps.production.transport_details_models import EquipmentDayDetails


class ExistingTransportImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):TransportImportTests.setUpTestData.__func__(cls)
    row=TransportImportTests.row
    def test_existing_master_is_reused_and_excel_values_reach_journal(self):
        machine=Equipment.objects.create(company=self.company,type=self.type,name='Заведена ранее',vin='VIN-100',inventory_number='INV-100',plate_number='A100')
        batch=service.preview(self.admin,[upload([self.row()])],self.day)
        self.assertEqual(batch.rows.get().equipment_id,machine.pk)
        count=Equipment.objects.count();service.apply(self.admin,batch)
        self.assertEqual(Equipment.objects.count(),count)
        self.assertEqual(EquipmentDayRecord.objects.get(equipment=machine).status,'WORKED')
        details=EquipmentDayDetails.objects.get(equipment=machine);self.assertEqual(str(details.repair_started_on),'2026-10-27');self.assertEqual(details.parts_stage.name,'В пути')
        machine.refresh_from_db();self.assertEqual(machine.name,'Заведена ранее')
    def test_old_error_queue_can_retry_existing_and_same_batch_repeat_stays_pending(self):
        machine=Equipment.objects.create(company=self.company,type=self.type,name='Заведена',vin='VIN-100',inventory_number='INV-100',plate_number='A100')
        batch=service.preview(self.admin,[upload([self.row(),self.row()])],self.day)
        # Simulate the error queue produced by the earlier importer.
        batch.rows.update(status='PENDING',equipment=None,error='VIN уже есть')
        from django.utils import timezone
        batch.applied_at=timezone.now();batch.save()
        self.assertEqual(service.retry_existing(self.admin),1)
        self.assertEqual(batch.rows.filter(status='APPLIED').count(),1);self.assertEqual(batch.rows.filter(status='PENDING').count(),1)
        self.assertEqual(Equipment.objects.filter(vin='VIN-100').count(),1)
