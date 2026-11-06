from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from .dispatch_models import EmployeeActivity, ProductionDailyCrew, ProductionDailyCrewMember


class DailyCrewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_production_entry import ProductionEntryTests
        ProductionEntryTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.user=self.users['DISPATCHER'];self.client.force_login(self.user);self.url_crews=reverse('production:production_crews')
        self.first=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=1,employee=self.employee,position=self.employee.position,entry_direction='works',assignment_reviewed=False,reported_by=self.user)
        self.second_person=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=1,employee=self.cook,position=self.cook.position,entry_direction='works',assignment_reviewed=False,reported_by=self.user)

    def post(self,action,**extra):
        return self.client.post(self.url_crews,{'construction_object':self.obj.pk,'date':str(self.day),'action':action,**extra},content_type='application/json')

    def test_create_automatic_number_move_and_delete_to_unassigned(self):
        first=self.post('create',shift=1,name='Монтажники');self.assertEqual(first.status_code,200,first.content)
        crew_id=first.json()['crews'][0]['id'];self.assertEqual(first.json()['crews'][0]['number'],1)
        self.assertEqual(self.post('move',activity=self.first.pk,crew=crew_id).status_code,200)
        second=self.post('create',shift=1);second_id=second.json()['crews'][1]['id']
        moved=self.post('move',activity=self.first.pk,crew=second_id);self.assertEqual(moved.status_code,200)
        self.assertEqual(ProductionDailyCrewMember.objects.get(activity=self.first).crew_id,second_id)
        deleted=self.post('delete',crew=second_id);self.assertFalse(ProductionDailyCrewMember.objects.filter(activity=self.first).exists())
        third=self.post('create',shift=1);self.assertEqual(third.json()['crews'][-1]['number'],3)

    def test_shift_mismatch_and_non_production_dispatcher_are_rejected(self):
        crew=self.post('create',shift=2).json()['crews'][0]
        response=self.post('move',activity=self.first.pk,crew=crew['id']);self.assertEqual(response.status_code,409)
        self.client.force_login(self.users['PERSONNEL_DISPATCHER']);self.assertEqual(self.post('create',shift=1).status_code,403)

    def test_bulk_move_adds_and_removes_selected_people(self):
        crew=self.post('create',shift=1).json()['crews'][0]
        response=self.post('bulk_move',activities=[self.first.pk,self.second_person.pk],crew=crew['id'])
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(ProductionDailyCrewMember.objects.filter(crew_id=crew['id']).count(),2)
        response=self.post('bulk_move',activities=[self.first.pk,self.second_person.pk],crew=None)
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(ProductionDailyCrewMember.objects.exists())

    def test_copy_previous_maps_only_people_already_entered_today(self):
        previous=self.day-timedelta(days=1)
        old_first=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=previous,shift=1,employee=self.employee,position=self.employee.position,entry_direction='works',reported_by=self.user)
        old_second=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=previous,shift=1,employee=self.cook,position=self.cook.position,entry_direction='works',reported_by=self.user)
        source=ProductionDailyCrew.objects.create(company=self.company,construction_object=self.obj,date=previous,shift=1,number=1,name='Вчера',reported_by=self.user)
        ProductionDailyCrewMember.objects.create(company=self.company,crew=source,activity=old_first)
        ProductionDailyCrewMember.objects.create(company=self.company,crew=source,activity=old_second)
        self.second_person.delete()
        response=self.post('copy_previous');self.assertEqual(response.status_code,200,response.content)
        copied=ProductionDailyCrew.objects.get(date=self.day,is_active=True);self.assertEqual(copied.name,'Вчера')
        self.assertEqual(list(copied.members.values_list('activity_id',flat=True)),[self.first.pk])
        self.assertIn('Не перенесены',response.json()['message'])

    def test_page_has_separate_optional_crew_step_without_foreman(self):
        response=self.client.get(self.url,{'construction_object':self.obj.pk,'date':self.day,'section':'works'})
        self.assertContains(response,'3. Бригады (необязательно)');self.assertContains(response,'Создать бригаду')
        self.assertNotContains(response,'Бригадир')
