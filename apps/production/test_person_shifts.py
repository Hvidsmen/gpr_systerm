from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .test_production_entry import ProductionEntryTests
from .dispatch_models import (EmployeeActivity, EmployeeWorkLink, EmployeeFactClaim,
    ProductionDailyCrew, ProductionDailyCrewMember, ProductionDailyCrewWorkLink)
from .models import DailyFact, LaborFact
from .crew_links import change_crew,change_individual


class ProductionPersonShiftTests(TestCase):
    setUpTestData=classmethod(ProductionEntryTests.setUpTestData.__func__)
    payload=ProductionEntryTests.payload

    def saved(self,shift=1,**extra):
        return EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,
            shift=shift,general=True,entry_direction='works',reported_by=self.users['DISPATCHER'],**extra)

    def claim(self,shift,session,user='DISPATCHER'):
        self.client.force_login(self.users[user])
        return self.client.post(reverse('production:work_claims'),{'construction_object':self.obj.pk,'date':str(self.day),'session':session,
            'key':f'employee:{self.employee.pk}:{shift}','action':'take'},content_type='application/json')

    def post_shift(self,shift,*,session=None,data=None):
        data=data or self.payload('DISPATCHER','works')
        data.update({'labor-0-employee':str(self.employee.pk),'labor-0-shift':str(shift),'labor-0-work_mode':'general'})
        if session:data.update({'autosave':'1','claim_session':session,'labor-0-person_claim_token':session})
        return self.client.post(self.url,data)

    def test_visible_day_night_choices_and_day_default(self):
        self.client.force_login(self.users['DISPATCHER'])
        page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'})
        form=next(g for g in page.context['groups'] if g['prefix']=='labor')['formset'].empty_form
        self.assertEqual(list(form.fields['shift'].choices),[(1,'День'),(2,'Ночь')]);self.assertEqual(form['shift'].value(),1)
        self.assertContains(page,'<option value="2">Ночь</option>',html=True);self.assertContains(page,'data-people-shift')
        self.assertNotContains(page,'type="number" min="1" max="24"')
    def test_new_night_fact_saved_and_displayed_after_reload(self):
        response=self.post_shift(2);self.assertEqual(response.status_code,302)
        row=EmployeeActivity.objects.get(employee=self.employee);self.assertEqual(row.shift,2);self.assertEqual(row.shift_label,'Ночь')
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
        self.assertEqual(self.payload('DISPATCHER','works')['labor-0-shift'],'2')
    def test_change_saved_shift_preserves_identity_work_links_and_headcount(self):
        row=self.saved();row.general=False;row.work=self.work;row.save()
        link=EmployeeWorkLink.objects.create(company=self.company,activity=row,work=self.work)
        data=self.payload('DISPATCHER','works');data['labor-0-shift']='2'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        row.refresh_from_db();self.assertEqual(row.shift,2);self.assertEqual(EmployeeActivity.objects.filter(employee=self.employee).count(),1)
        self.assertTrue(EmployeeWorkLink.objects.filter(pk=link.pk,activity=row).exists());self.assertEqual(row.reported_by_id,self.users['DISPATCHER'].pk)
        self.assertEqual(LaborFact.objects.get().actual_workers,1)
        data=self.payload('DISPATCHER','works');data['labor-0-shift']='1'
        self.assertEqual(self.client.post(self.url,data).status_code,302);row.refresh_from_db();self.assertEqual(row.shift,1)
    def test_change_shift_removes_old_crew_membership_and_only_crew_owned_links(self):
        row=self.saved();row.general=False;row.save()
        crew=ProductionDailyCrew.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=1,number=1,reported_by=self.users['DISPATCHER'])
        ProductionDailyCrewMember.objects.create(company=self.company,crew=crew,activity=row)
        crew_fact,_=DailyFact.objects.get_or_create(company=self.company,date=self.day,project_work=self.work,defaults={'actual_quantity':1,'reported_by':self.users['DISPATCHER']})
        personal_fact,_=DailyFact.objects.get_or_create(company=self.company,date=self.day,project_work=self.second_work,defaults={'actual_quantity':1,'reported_by':self.users['DISPATCHER']})
        change_crew(row,crew,crew_fact,'add');change_individual(row,personal_fact,'add')
        data=self.payload('DISPATCHER','works');data['labor-0-shift']='2'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        row.refresh_from_db();self.assertEqual(row.shift,2)
        self.assertFalse(ProductionDailyCrewMember.objects.filter(activity=row).exists())
        self.assertFalse(EmployeeWorkLink.objects.filter(activity=row,work=self.work).exists())
        self.assertTrue(EmployeeWorkLink.objects.filter(activity=row,work=self.second_work,assigned_individually=True).exists())
        self.assertTrue(ProductionDailyCrewWorkLink.objects.filter(crew=crew,fact=crew_fact).exists())
    def test_autosave_transition_with_source_and_destination_claims(self):
        row=self.saved();data=self.payload('DISPATCHER','works');session=str(uuid4())
        self.assertEqual(self.claim(1,session).status_code,200);self.assertEqual(self.claim(2,session).status_code,200)
        response=self.post_shift(2,session=session,data=data);self.assertEqual(response.status_code,200,response.content[:1000])
        row.refresh_from_db();self.assertEqual(row.shift,2);self.assertEqual(response.json()['rows'][0]['id'],row.pk)
    def test_target_reserved_by_colleague_does_not_change_saved_shift(self):
        row=self.saved();data=self.payload('DISPATCHER','works');session=str(uuid4())
        self.claim(2,str(uuid4()),user='SECOND');self.claim(1,session)
        response=self.post_shift(2,session=session,data=data);self.assertEqual(response.status_code,400)
        row.refresh_from_db();self.assertEqual(row.shift,1);self.assertEqual(EmployeeActivity.objects.count(),1)
    def test_source_reserved_by_colleague_blocks_even_if_destination_is_free(self):
        row=self.saved();data=self.payload('DISPATCHER','works');session=str(uuid4())
        self.claim(1,str(uuid4()),user='SECOND');self.claim(2,session)
        response=self.post_shift(2,session=session,data=data);self.assertEqual(response.status_code,400)
        row.refresh_from_db();self.assertEqual(row.shift,1)
    def test_target_already_saved_in_same_or_other_object_is_not_overwritten(self):
        for obj in [self.obj,self.other]:
            EmployeeActivity.objects.all().delete();row=self.saved()
            target=EmployeeActivity.objects.create(company=self.company,construction_object=obj,date=self.day,employee=self.employee,shift=2,general=True,entry_direction='works')
            response=self.post_shift(2);self.assertEqual(response.status_code,400)
            row.refresh_from_db();target.refresh_from_db();self.assertEqual(row.shift,1);self.assertEqual(target.shift,2)
    def test_invalid_shift_rejected_but_legacy_shift_can_be_kept_or_changed(self):
        self.assertEqual(self.post_shift(3).status_code,400);self.assertFalse(EmployeeActivity.objects.exists())
        row=self.saved(shift=3);data=self.payload('DISPATCHER','works')
        self.assertEqual(data['labor-0-shift'],'3');data['labor-0-activity']='Проверено'
        self.assertEqual(self.client.post(self.url,data).status_code,302)
        self.assertEqual(self.post_shift(2).status_code,302);row.refresh_from_db();self.assertEqual(row.shift,2)
    def test_copy_previous_keeps_night_without_changing_source(self):
        from .production_entry import previous_people
        row=self.saved(shift=2);row.date=self.day-timedelta(days=1);row.save()
        day,values=previous_people(self.obj,self.day,production=True,user=self.users['DISPATCHER'])
        self.assertEqual(day,row.date);self.assertEqual(values[0]['shift'],2);row.refresh_from_db();self.assertEqual(row.shift,2)
    def test_stale_row_does_not_overwrite_colleague_shift_edit(self):
        row=self.saved();first=self.payload('DISPATCHER','works');second=self.payload('DISPATCHER','works')
        first['labor-0-shift']='2';self.assertEqual(self.client.post(self.url,first).status_code,302)
        second['labor-0-activity']='Старый ввод';self.assertEqual(self.client.post(self.url,second).status_code,400)
        row.refresh_from_db();self.assertEqual(row.shift,2);self.assertEqual(row.activity,'')
    def test_report_export_uses_day_night_labels(self):
        from io import BytesIO
        from openpyxl import load_workbook
        self.saved(shift=2)
        self.client.force_login(self.users['DISPATCHER'])
        # Use the same export endpoint as the daily report.
        page=self.client.get(self.url,{'construction_object':self.obj.pk,'date':str(self.day),'section':'works'})
        route=reverse('production:production_report_download')
        response=self.client.get(route,{'construction_object':self.obj.pk,'date':str(self.day)})
        self.assertEqual(response.status_code,200)
        wb=load_workbook(BytesIO(b''.join(response.streaming_content)));self.assertTrue(any(cell.value=='Ночь' for sheet in wb for cells in sheet for cell in cells))
