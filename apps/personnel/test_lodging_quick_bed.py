import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.production.history_models import FactRevision
from .test_lodging import LodgingTests
from .models import LodgingBed, LodgingBuilding


class QuickBedTests(TestCase):
    setUpTestData=classmethod(LodgingTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.commandant)
        self.url=reverse('personnel:lodging_quick_bed')
    def payload(self,**kwargs):
        return {'camp':self.a.pk,'date':str(self.day),'building':self.building.pk,'number':'9','room':'2',**kwargs}
    def test_create_in_existing_building_is_audited_and_ready_for_selection(self):
        response=self.client.post(self.url,self.payload(note='Новое место'))
        self.assertEqual(response.status_code,201)
        bed=LodgingBed.objects.get(pk=response.json()['id'])
        self.assertEqual((bed.company,bed.building,bed.room,bed.number,bed.state),(self.company,self.building,'2','9','AVAILABLE'))
        self.assertIn('место 9',response.json()['label'])
        self.assertTrue(FactRevision.objects.filter(entity_type='personnel.lodgingbed',entity_id=str(bed.pk),actor=self.commandant).exists())
    def test_can_create_building_and_place_together(self):
        response=self.client.post(self.url,self.payload(building='',new_building_name='Новый вагон',new_building_inventory_number='W-NEW'))
        self.assertEqual(response.status_code,201)
        bed=LodgingBed.objects.get(pk=response.json()['id'])
        self.assertEqual((bed.building.name,bed.building.inventory_number,bed.building.camp),('Новый вагон','W-NEW',self.a))
    def test_missing_number_conflicting_building_and_duplicate_do_not_add_rows(self):
        before=LodgingBed.objects.count()
        for values in [self.payload(number=''),self.payload(new_building_name='Лишний вагон'),self.payload(number='1',room='')]:
            self.assertEqual(self.client.post(self.url,values).status_code,400)
        self.assertEqual(LodgingBed.objects.count(),before)
        self.assertFalse(LodgingBuilding.objects.filter(name='Лишний вагон').exists())
    def test_invalid_bed_rolls_back_new_building(self):
        with patch('apps.personnel.models.LodgingBed.save',side_effect=ValidationError('Место изменено')):
            response=self.client.post(self.url,self.payload(building='',new_building_name='Откат'))
        self.assertEqual(response.status_code,400)
        self.assertFalse(LodgingBuilding.objects.filter(name='Откат').exists())
    def test_company_camp_and_operator_permissions(self):
        self.assertEqual(self.client.get(self.url).status_code,405)
        self.assertEqual(self.client.post(self.url,self.payload(building=self.foreign_building.pk)).status_code,400)
        self.assertEqual(self.client.post(self.url,self.payload(camp=self.b.pk)).status_code,400)
        self.assertEqual(self.client.post(self.url,self.payload(camp=self.foreign_camp.pk)).status_code,404)
        self.client.force_login(self.dispatcher)
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,403)


@skipUnless(os.environ.get('QUICK_BED_BROWSER_TESTS')=='1','Optional inline lodging place Chromium scenario')
class QuickBedBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_create_place_without_losing_movement_input(self):
        LodgingTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.commandant)
        config={'base':self.live_server_url,'url':reverse('personnel:lodging'),'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'camp':self.a.pk,'day':str(self.day),'employee':self.second_employee.pk,'bed':self.bed.pk}
        script=Path(__file__).resolve().parents[2]/'tests/browser/lodging_quick_bed.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,QUICK_BED_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
