import json, os, shutil, subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import TestCase
from django.urls import reverse
from .test_housing import HousingTests
from .models import CampStay, HousingEvent, StaffingSlot
from . import housing


class MovementActionTests(TestCase):
    setUpTestData=classmethod(HousingTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.hr)
        self.url=reverse('personnel:housing')
        self.unassigned=StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,status='МВ',pattern=self.pattern,arrival_date=self.day)

    def payload(self, kind, employee=None, camp=None, **extra):
        return {'camp':(camp or self.a).pk,'kind':kind,'date':str(self.day),'employees':[employee or self.employee.pk], 'snapshot':housing.movement_snapshot(self.hr),**extra}

    def test_unassigned_employee_has_arrival_button_and_must_choose_camp(self):
        page=self.client.get(self.url,{'date':str(self.day)})
        self.assertContains(page,f'choose_camp=1&amp;date={self.day}&amp;action=ARRIVAL&amp;employee={self.second_employee.pk}#movement')
        page=self.client.get(self.url,{'date':str(self.day),'action':'ARRIVAL','employee':self.second_employee.pk,'choose_camp':'1'})
        self.assertIsNone(page.context['form']['camp'].value())
        payload=self.payload('ARRIVAL',employee=self.second_employee.pk);payload['camp']=''
        response=self.client.post(self.url,payload)
        self.assertIn('camp',response.context['form'].errors)
        self.assertFalse(CampStay.objects.filter(person=self.second_person).exists())

    def test_arrival_uses_explicit_camp_and_preserves_initial_table(self):
        self.assertEqual(self.client.post(self.url,self.payload('ARRIVAL',employee=self.second_employee.pk,camp=self.b)).status_code,302)
        self.assertEqual(CampStay.objects.get(person=self.second_person).camp,self.b)
        self.unassigned.refresh_from_db();self.assertIsNone(self.unassigned.housing_camp_id)
        self.assertEqual(self.unassigned.person,self.second_person)

    def test_dismissal_is_departure_reason_without_changing_employment_or_slot(self):
        response=self.client.post(self.url,self.payload('DISMISSAL',comment='Выезд по увольнению'))
        self.assertEqual(response.status_code,302)
        event=HousingEvent.objects.get(person=self.person)
        self.assertEqual((event.kind,event.departure_destination,event.date),('DEPARTURE','DISMISSAL',self.day))
        self.assertEqual(event.recorded_by,self.hr)
        self.stay.refresh_from_db();self.assertEqual(self.stay.departed_on,self.day)
        self.employee.refresh_from_db();self.assertTrue(self.employee.is_active)
        self.slot.refresh_from_db();self.assertEqual(self.slot.person,self.person)
        self.assertContains(self.client.get(reverse('personnel:housing_history',args=[self.person.pk])),'По увольнению')

    def test_dismissal_available_in_departure_reasons_too(self):
        response=self.client.post(self.url,self.payload('DEPARTURE',destination='DISMISSAL'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(HousingEvent.objects.get(person=self.person).departure_destination,'DISMISSAL')

    def test_dismissal_without_current_presence_does_not_create_exit(self):
        response=self.client.post(self.url,self.payload('DISMISSAL',employee=self.second_employee.pk))
        self.assertContains(response,'находящихся в этом ВЖГ')
        self.assertFalse(HousingEvent.objects.exists())
        self.assertFalse(CampStay.objects.filter(person=self.second_person).exists())

    def test_foreign_camp_and_stale_snapshot_do_not_change_presence(self):
        payload=self.payload('ARRIVAL',employee=self.second_employee.pk);payload['camp']=self.foreign_camp.pk
        self.assertEqual(self.client.post(self.url,payload).status_code,404)
        stale=self.payload('DISMISSAL')
        housing.move(self.hr2,'TRANSFER',self.a,self.day,[self.person],target=self.b)
        response=self.client.post(self.url,stale)
        self.assertEqual(response.status_code,200)
        self.assertFalse(HousingEvent.objects.filter(departure_destination='DISMISSAL').exists())


@skipUnless(os.environ.get('MOVEMENT_ACTION_BROWSER_TESTS')=='1','Optional movement action browser scenario')
class MovementActionBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_unassigned_arrival_and_dismissal_reason(self):
        HousingTests.setUpTestData.__func__(type(self))
        StaffingSlot.objects.create(company=self.company,group=self.group,person=self.second_person,status='МВ',pattern=self.pattern,arrival_date=self.day)
        self.client.force_login(self.hr)
        config={'base':self.live_server_url,'url':reverse('personnel:housing'),'cookie':self.client.cookies['sessionid'].value,'chromium':shutil.which('chromium'),'employee':self.second_employee.pk,'person':self.second_person.pk,'camp':self.b.pk,'day':str(self.day)}
        script=Path(__file__).resolve().parents[2]/'tests/browser/movement_actions.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,MOVEMENT_ACTION_CONFIG=json.dumps(config)),timeout=90)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
