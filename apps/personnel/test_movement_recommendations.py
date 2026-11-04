from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Role,User
from .models import MovementRecommendationOverride
from .movement_recommendations import add,build,exclude


class MovementRecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)
        cls.transport=User.objects.create_user(username='transport-people',company=cls.company,role=Role.objects.get(code='TRANSPORT_DISPATCHER'))

    def auto_rows(self):
        return [
            {'person':self.second_person,'slot':self.slot,'camp':self.a,'state':'MO','planned_return':self.day+timedelta(days=3),'planned_departure_display':None},
            {'person':self.person,'slot':self.slot,'camp':self.a,'state':'MV','planned_return':None,'planned_departure_display':self.day+timedelta(days=5)},
        ],[],{}

    @patch('apps.personnel.rotation.rotation_rows')
    def test_schedule_recommendations_can_be_excluded_for_every_dispatcher(self,rotation_rows):
        rotation_rows.return_value=self.auto_rows()
        result=build(self.company,self.day)
        self.assertEqual([row['person'] for row in result['ARRIVAL']],[self.second_person])
        self.assertEqual([row['person'] for row in result['DEPARTURE']],[self.person])
        self.assertEqual(exclude(self.transport,[result['ARRIVAL'][0]['token']]),1)
        self.assertEqual(build(self.company,self.day)['ARRIVAL'],[])
        override=MovementRecommendationOverride.objects.get();self.assertEqual(override.action,'EXCLUDE');self.assertEqual(override.recorded_by,self.transport)

    @patch('apps.personnel.rotation.rotation_rows',return_value=([],[],{}))
    def test_manual_arrival_is_shared_and_actual_presence_hides_it(self,rotation_rows):
        planned=self.day+timedelta(days=2)
        self.assertEqual(add(self.transport,'ARRIVAL',[self.second_employee],self.a,planned),1)
        result=build(self.company,self.day);self.assertEqual(len(result['ARRIVAL']),1);self.assertTrue(result['ARRIVAL'][0]['manual'])
        from . import housing
        housing.move(self.hr,'ARRIVAL',self.a,self.day,[self.second_person])
        self.assertEqual(build(self.company,self.day)['ARRIVAL'],[])

    def test_transport_dispatcher_has_only_movement_workspace(self):
        self.client.force_login(self.transport)
        response=self.client.get(reverse('personnel:movements'))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'1. Рекомендации к заезду')
        self.assertContains(response,'2. Рекомендации к выезду')
        self.assertContains(response,'3. Фактический заезд или выезд')
        self.assertEqual(self.client.get(reverse('personnel:housing')).status_code,403)

    def test_transport_dispatcher_can_save_bulk_current_movement_but_not_old_fact(self):
        self.client.force_login(self.transport);today=timezone.localdate();url=reverse('personnel:movements')
        page=self.client.get(url,{'camp':self.a.pk});snapshot=page.context['form'].initial['snapshot']
        data={'workflow_action':'move','camp':self.a.pk,'kind':'ARRIVAL','date':today,'employees':[self.second_employee.pk],'snapshot':snapshot}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertTrue(self.second_person.stays.filter(camp=self.a,arrived_on=today,cancelled_at=None).exists())
        data.update(kind='DEPARTURE',date=today-timedelta(days=2),snapshot=page.context['form'].initial['snapshot'])
        self.assertEqual(self.client.post(url,data).status_code,403)

    def test_recommendation_date_is_limited_to_fourteen_days(self):
        self.client.force_login(self.transport)
        response=self.client.post(reverse('personnel:movements'),{
            'workflow_action':'recommendation_add','recommendation_kind':'ARRIVAL',
            'arrival-employees':[self.second_employee.pk],'arrival-camp':self.a.pk,
            'arrival-planned_on':timezone.localdate()+timedelta(days=15),
        })
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'пределах ближайших 14 дней')
        self.assertFalse(MovementRecommendationOverride.objects.exists())
