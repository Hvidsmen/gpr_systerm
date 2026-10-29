from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook
from apps.accounts.models import User,Role
from .test_housing import HousingTests
from .models import LodgingStay,LodgingBed,LodgingBuilding,LodgingEvent,LodgingImportBatch,LodgingImportRow,CampStay,HousingEvent
from . import lodging_import as service


def workbook(day,names):
    book=Workbook();sheet=book.active;sheet.title=service.SHEET
    sheet.append(['Дата',None,None,None,day]);sheet.append(['ИТОГО',None,len([n for n in names if n])])
    sheet.append(['№ п.п','№ места','Инв.','Тип','ФИО','Организация','Должность','Ключи','Прибыл','Убытие'])
    for index,name in enumerate(names,1):sheet.append(['Вагон 1' if index==1 else None,index,'Инв. АБ-001' if index==1 else None,'Жилой',name,'Организация','Рабочий','Да',day-timedelta(days=5) if name else None])
    out=BytesIO();book.save(out);return out.getvalue()


class LodgingImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        HousingTests.setUpTestData.__func__(cls)
        cls.admin=User.objects.create_user(username='import-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
    def setUp(self):
        self.client.force_login(self.admin);self.raw=workbook(self.day,[self.employee.display_name,'Не найден',None])
    def send(self,names=None,camp=None):
        return self.client.post(reverse('personnel:lodging_import'),{'camp':(camp or self.a).pk,'file':SimpleUploadedFile('Шахматка.xlsx',workbook(self.day,names) if names else self.raw)})
    def test_partial_import_is_queued_and_retry_is_idempotent_without_hr_movements(self):
        before=CampStay.objects.count();events=HousingEvent.objects.count()
        self.assertEqual(self.send().status_code,302)
        batch=LodgingImportBatch.objects.get();self.assertEqual(batch.rows.count(),3)
        self.assertEqual(LodgingBed.objects.count(),3);self.assertEqual(LodgingStay.objects.count(),1)
        self.assertEqual(LodgingBuilding.objects.get().name,'Вагон 1');self.assertEqual(LodgingBuilding.objects.get().description,'Инв. АБ-001')
        self.assertEqual(LodgingBuilding.objects.get().inventory_number,'АБ-001')
        stay=LodgingStay.objects.get();self.assertEqual(stay.arrived_on,self.day-timedelta(days=5));self.assertEqual(stay.recorded_by,self.admin)
        self.assertEqual(stay.organization_snapshot,'Организация');self.assertTrue(stay.keys_issued)
        self.assertEqual(LodgingEvent.objects.count(),1);self.assertEqual(CampStay.objects.count(),before);self.assertEqual(HousingEvent.objects.count(),events)
        pending=batch.rows.get(state='PENDING');self.assertIn('не найден',pending.warning)
        self.assertEqual(self.send().status_code,302);self.assertEqual(LodgingImportBatch.objects.count(),1);self.assertEqual(LodgingStay.objects.count(),1)
        self.second_employee.last_name='Не';self.second_employee.first_name='найден';self.second_employee.save()
        self.client.post(reverse('personnel:lodging_import_review',args=[batch.pk]))
        self.assertEqual(batch.rows.filter(state='PENDING').count(),0);self.assertEqual(LodgingStay.objects.count(),2)
    def test_only_admin_and_same_company_can_import_or_review(self):
        self.assertEqual(self.send().status_code,302);batch=LodgingImportBatch.objects.get()
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse('personnel:lodging_import')).status_code,403)
        self.assertEqual(self.client.post(reverse('personnel:lodging_import_review',args=[batch.pk])).status_code,403)
        self.client.force_login(self.admin);self.assertContains(self.send(camp=self.foreign_camp),'Выберите корректный вариант')
    def test_reupload_enriches_legacy_key_metadata_without_reentering_stay(self):
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(workbook(self.day,[self.employee.display_name])))
        book.active.cell(4,8,'КЛЮЧ 2/19');book.active.cell(4,12,'P-19');out=BytesIO();book.save(out);self.raw=out.getvalue()
        with patch.object(service,'numbered_key',return_value=''):self.send()
        stay=LodgingStay.objects.get();self.assertFalse(stay.keys_issued)
        before=LodgingEvent.objects.count();self.send();stay.refresh_from_db()
        self.assertEqual(stay.key_number,'КЛЮЧ 2/19');self.assertTrue(stay.keys_issued);self.assertEqual(stay.pass_number,'P-19')
        self.assertEqual(LodgingStay.objects.count(),1);self.assertEqual(LodgingEvent.objects.count(),before)
        # A later operator correction remains authoritative on another re-upload.
        stay.key_number='';stay.keys_issued=False;stay.save();self.send();stay.refresh_from_db()
        self.assertEqual(stay.key_number,'КЛЮЧ 2/19');self.assertFalse(stay.keys_issued)
    def test_conflicting_occupancy_does_not_change_existing_stay(self):
        self.send([self.employee.display_name]);stay=LodgingStay.objects.get();original=stay.updated_at
        raw=workbook(self.day,[None,self.employee.display_name])
        self.client.post(reverse('personnel:lodging_import'),{'camp':self.a.pk,'file':SimpleUploadedFile('Другая.xlsx',raw)})
        self.assertEqual(LodgingStay.objects.count(),1);stay.refresh_from_db();self.assertEqual(stay.updated_at,original)
        self.assertTrue(LodgingImportRow.objects.filter(state='PENDING',warning__icontains='уже есть проживание').exists())
    def test_failure_after_stay_save_rolls_back_occupancy_but_keeps_review_row(self):
        with patch('apps.personnel.lodging_import.LodgingEvent.objects.create',side_effect=ValidationError('Конфликт записи события')):
            self.send([self.employee.display_name])
        self.assertEqual(LodgingStay.objects.count(),0);self.assertEqual(LodgingEvent.objects.count(),0);self.assertEqual(LodgingBed.objects.count(),1)
        self.assertIn('Конфликт',LodgingImportRow.objects.get().warning)
    def test_parser_requires_sheet_date_and_consistent_total(self):
        parsed=service.parse(workbook(self.day,[self.employee.display_name,None]))
        self.assertEqual((parsed['people'],parsed['buildings'],parsed['beds']),(1,1,2))
        with self.assertRaises(ValidationError):service.parse(b'not xlsx')

    def test_missing_bed_and_future_arrival_are_queued_without_blocking_valid_people(self):
        from openpyxl import load_workbook
        raw=workbook(self.day,[self.employee.display_name,self.second_employee.display_name,'Человек без места'])
        book=load_workbook(BytesIO(raw));sheet=book.active;sheet.cell(5,9,self.day+timedelta(days=1));sheet.cell(6,2).value=None
        out=BytesIO();book.save(out)
        parsed=service.parse(out.getvalue());self.assertEqual((parsed['people'],parsed['beds']),(3,2))
        response=self.client.post(reverse('personnel:lodging_import'),{'camp':self.a.pk,'file':SimpleUploadedFile('Проверка.xlsx',out.getvalue())})
        self.assertEqual(response.status_code,302);self.assertEqual(LodgingStay.objects.count(),1)
        self.assertEqual(LodgingImportRow.objects.filter(state='PENDING').count(),2)


    def test_wagon_description_is_in_header_and_not_bed_notes(self):
        from openpyxl import load_workbook
        description='Вагон-дом жилой на 8 чел на санях 1218120 БП-002666'
        book=load_workbook(BytesIO(workbook(self.day,[None,None])))
        book.active.cell(4,3,description);book.active.cell(4,4,'РАБ М');book.active.cell(5,4,'РАБ М')
        out=BytesIO();book.save(out);self.raw=out.getvalue()
        parsed=service.parse(self.raw)
        self.assertTrue(all(row['description']==description and row['note']=='РАБ М' for row in parsed['rows']))
        self.send();building=LodgingBuilding.objects.get()
        self.assertEqual(building.name,'Вагон 1');self.assertEqual(building.description,description)
        self.assertEqual(list(LodgingBed.objects.values_list('note',flat=True)),['РАБ М','РАБ М'])
        page=self.client.get(reverse('personnel:lodging'),{'camp':self.a.pk,'date':str(self.day)})
        self.assertContains(page,'data-building-description>'+description+'</span>',html=False)
        self.assertEqual(service.bed_note({'note':description+' · РАБ М'},description),'РАБ М')
        book.active.cell(4,3,'Вагон-дом заказчика');out=BytesIO();book.save(out)
        self.assertEqual(service.parse(out.getvalue())['rows'][0]['bed_state'],'RESERVED')

    def test_legacy_wagon_notes_are_promoted_without_overwriting_manual_description(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        migrate=import_module('apps.personnel.migrations.0011_move_wagon_description_from_bed_notes').forwards
        description='Вагон-дом жилой на 8 чел на санях 1218120 БП-002666'
        wagon=LodgingBuilding.objects.create(company=self.company,camp=self.a,name='1')
        beds=[LodgingBed.objects.create(company=self.company,building=wagon,number=str(i),note=note) for i,note in enumerate([description+' · РАБ М',description,'Сломан замок'],1)]
        manual=LodgingBuilding.objects.create(company=self.company,camp=self.a,name='2',description='Уточнено комендантом')
        keep=LodgingBed.objects.create(company=self.company,building=manual,number='1',note=description+' · ИТР')
        migrate(apps,SimpleNamespace(connection=connection));migrate(apps,SimpleNamespace(connection=connection))
        wagon.refresh_from_db();manual.refresh_from_db();keep.refresh_from_db()
        self.assertEqual(wagon.description,description);self.assertEqual(manual.description,'Уточнено комендантом')
        self.assertEqual(list(wagon.beds.order_by('number').values_list('note',flat=True)),['РАБ М','','Сломан замок'])
        self.assertEqual(keep.note,description+' · ИТР')
