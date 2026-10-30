from django.test import TestCase
from django.urls import reverse
from . import test_transport_import as fixtures
from . import transport_import as service
from .transport_import_models import TransportImportFile
from apps.accounts.models import User,Company,Role

class TransportSourceTests(TestCase):
    setUpTestData=classmethod(fixtures.TransportImportTests.setUpTestData.__func__)
    row=fixtures.TransportImportTests.row
    def test_download_is_original_bytes_deduplicated_and_company_scoped(self):
        upload=fixtures.upload([self.row(year='190')],'Источник.xlsm');raw=upload.read();upload.seek(0)
        batch=service.preview(self.admin,[upload],self.day);row=batch.rows.get();self.assertTrue(row.source_id)
        from .transport_import_models import TransportImportRow
        reloaded=TransportImportRow.objects.get(pk=row.pk)
        self.assertIn('data',reloaded.source.get_deferred_fields())
        again=service.preview(self.admin,[fixtures.upload([self.row(year='190')],'Источник.xlsm')],self.day)
        # Workbook ZIP creation timestamps may differ, so verify the exact same bytes.
        from django.core.files.uploadedfile import SimpleUploadedFile
        service.preview(self.admin,[SimpleUploadedFile('Копия.xlsm',raw)],self.day)
        self.assertEqual(TransportImportFile.objects.filter(sha256=row.source.sha256).count(),1)
        self.client.force_login(self.operator);response=self.client.get(reverse('resources:transport_import_source',args=[row.pk]));self.assertEqual(response.status_code,200);self.assertEqual(b''.join(response.streaming_content),raw);self.assertIn('attachment',response['Content-Disposition'])
        foreign=User.objects.create_user(username='foreign-source',company=Company.objects.create(name='Foreign'),role=Role.objects.get(code='MECHANIZATION_DISPATCHER'))
        self.client.force_login(foreign);self.assertEqual(self.client.get(reverse('resources:transport_import_source',args=[row.pk])).status_code,404)
        self.client.force_login(self.users['DISPATCHER']);self.assertEqual(self.client.get(reverse('resources:transport_import_source',args=[row.pk])).status_code,403)
