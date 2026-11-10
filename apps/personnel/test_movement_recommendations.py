from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Role,User
from .models import MovementRecommendationOverride,MovementReviewRequest
from .movement_recommendations import add,build,create_requests,exclude


class MovementRecommendationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_housing import HousingTests
        HousingTests.setUpTestData.__func__(cls)
        cls.personnel_dispatcher=cls.hr

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
        self.assertEqual(exclude(self.personnel_dispatcher,[result['ARRIVAL'][0]['token']]),1)
        self.assertEqual(build(self.company,self.day)['ARRIVAL'],[])
        override=MovementRecommendationOverride.objects.get();self.assertEqual(override.action,'EXCLUDE');self.assertEqual(override.recorded_by,self.personnel_dispatcher)

    @patch('apps.personnel.rotation.rotation_rows')
    def test_departures_include_overdue_and_fourteenth_day_but_not_day_fifteen(self,rotation_rows):
        def row(person,planned):
            return {'person':person,'slot':self.slot,'camp':self.a,'state':'MV','planned_return':None,
                'planned_departure_display':planned}
        rotation_rows.return_value=([row(self.person,self.day-timedelta(days=36)),
            row(self.second_person,self.day+timedelta(days=14))],[],{})
        result=build(self.company,self.day)
        self.assertEqual([(item['person'],item['planned_on']) for item in result['DEPARTURE']],
            [(self.person,self.day-timedelta(days=36)),(self.second_person,self.day+timedelta(days=14))])
        rotation_rows.return_value=([row(self.person,self.day+timedelta(days=15))],[],{})
        self.assertEqual(build(self.company,self.day)['DEPARTURE'],[])

    @patch('apps.personnel.rotation.rotation_rows')
    def test_overdue_departure_stays_hidden_after_exclusion_or_request(self,rotation_rows):
        planned=self.day-timedelta(days=5)
        rotation_rows.return_value=([{'person':self.person,'slot':self.slot,'camp':self.a,'state':'MV',
            'planned_return':None,'planned_departure_display':planned}],[],{})
        recommendation=build(self.company,self.day)['DEPARTURE'][0]
        exclude(self.personnel_dispatcher,[recommendation['token']])
        self.assertEqual(build(self.company,self.day)['DEPARTURE'],[])

    @patch('apps.personnel.rotation.rotation_rows',return_value=([],[],{}))
    def test_manual_arrival_is_shared_and_actual_presence_hides_it(self,rotation_rows):
        planned=self.day+timedelta(days=2)
        self.assertEqual(add(self.personnel_dispatcher,'ARRIVAL',[self.second_employee],self.a,planned),1)
        result=build(self.company,self.day);self.assertEqual(len(result['ARRIVAL']),1);self.assertTrue(result['ARRIVAL'][0]['manual'])
        from . import housing
        housing.move(self.hr,'ARRIVAL',self.a,self.day,[self.second_person])
        self.assertEqual(build(self.company,self.day)['ARRIVAL'],[])

    @patch('apps.personnel.rotation.rotation_rows')
    def test_recommendation_can_be_converted_to_request(self,rotation_rows):
        self.second_employee.phone='+7 999 000-11-22';self.second_employee.save(update_fields=['phone'])
        rotation_rows.return_value=self.auto_rows();result=build(self.company,self.day);row=result['ARRIVAL'][0]
        self.assertEqual(create_requests(self.personnel_dispatcher,[row['token']]),1)
        request=MovementReviewRequest.objects.get()
        self.assertEqual((request.person,request.kind,request.to_camp,request.movement_date),(self.second_person,'ARRIVAL',self.a,row['planned_on']))
        self.assertEqual(request.phone,self.second_employee.phone)
        self.assertEqual(request.status,'PENDING_APPROVAL');self.assertIsNotNone(request.approval_batch)
        self.assertEqual(build(self.company,self.day)['ARRIVAL'],[])

    def test_personnel_dispatcher_has_dispatch_and_rotation_workspaces(self):
        self.client.force_login(self.personnel_dispatcher)
        arrival=self.client.get(reverse('personnel:arrivals'));departure=self.client.get(reverse('personnel:departures'));request=self.client.get(reverse('personnel:movements'))
        self.assertEqual(arrival.context['movement_page'],'arrival');self.assertEqual(departure.context['movement_page'],'departure');self.assertEqual(request.context['movement_page'],'new')
        self.assertContains(arrival,'Рекомендации к заезду');self.assertNotContains(arrival,'Рекомендации к выезду')
        self.assertNotContains(arrival,'>Перевахта<');self.assertNotContains(arrival,'>Магазин подбора<')
        self.assertContains(departure,'Рекомендации к выезду');self.assertNotContains(departure,'Рекомендации к заезду')
        self.assertNotContains(arrival,'data-movement-form');self.assertNotContains(departure,'data-movement-form')
        self.assertContains(request,'Заявка на заезд или выезд');self.assertNotContains(request,'Рекомендации к заезду')
        self.assertContains(arrival,'Состав, назначения и графики')
        self.assertEqual(self.client.get(reverse('personnel:housing')).status_code,200)

    @patch('apps.personnel.movement_recommendations.build')
    def test_roster_navigation_counter_uses_company_wide_recommendations(self,recommendations):
        recommendations.return_value={'ARRIVAL':[],'DEPARTURE':[{'person':self.person}]}
        self.client.force_login(self.personnel_dispatcher)
        roster=self.client.get(reverse('personnel:housing'),{'camp':self.a.pk})
        self.assertEqual(roster.context['departure_recommendation_count'],1)
        recommendations.assert_called_once_with(self.company,timezone.localdate())

    @patch('apps.personnel.rotation.rotation_rows')
    def test_recommendation_tables_have_select_all_checkbox(self,rotation_rows):
        rotation_rows.return_value=self.auto_rows();self.client.force_login(self.personnel_dispatcher)
        for route in ('arrivals','departures'):
            response=self.client.get(reverse('personnel:'+route));self.assertEqual(response.status_code,200)
            self.assertContains(response,'data-recommendation-select-all',count=1)
            self.assertContains(response,'aria-label="Выбрать всех сотрудников"',count=1)

    def test_personnel_dispatcher_can_submit_bulk_current_request_and_old_date_needs_reason(self):
        self.client.force_login(self.personnel_dispatcher);today=timezone.localdate();url=reverse('personnel:movements')
        page=self.client.get(url,{'camp':self.a.pk});snapshot=page.context['form'].initial['snapshot']
        data={'workflow_action':'move','camp':self.a.pk,'kind':'ARRIVAL','date':today,'employees':[self.second_employee.pk],'snapshot':snapshot}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertFalse(self.second_person.stays.exists())
        self.assertTrue(MovementReviewRequest.objects.filter(person=self.second_person,kind='ARRIVAL',movement_date=today,status='PENDING_APPROVAL').exists())
        data.update(kind='DEPARTURE',date=today-timedelta(days=2),snapshot=page.context['form'].initial['snapshot'])
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'комментарий-основание')

    def test_personnel_dispatcher_can_temporarily_record_arrival_and_departure_without_requests(self):
        self.client.force_login(self.personnel_dispatcher);today=timezone.localdate();arrival=today-timedelta(days=5);url=reverse('personnel:movements')
        page=self.client.get(url,{'camp':self.a.pk})
        data={'workflow_action':'move','direct_fact':'on','camp':self.a.pk,'kind':'ARRIVAL','date':arrival,
            'employees':[self.second_employee.pk],'snapshot':page.context['form'].initial['snapshot'],'comment':'Уточнение по ведомости'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertTrue(self.second_person.stays.filter(camp=self.a,arrived_on=arrival,departed_on=None).exists())
        self.assertFalse(MovementReviewRequest.objects.exists())

        page=self.client.get(url,{'camp':self.a.pk})
        data.update(kind='DEPARTURE',date=today,snapshot=page.context['form'].initial['snapshot'])
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertTrue(self.second_person.stays.filter(camp=self.a,arrived_on=arrival,departed_on=today).exists())
        self.assertEqual(list(self.second_person.housing_events.values_list('kind','date')),[('DEPARTURE',today),('ARRIVAL',arrival)])
        self.assertFalse(MovementReviewRequest.objects.exists())

    def test_recommendation_date_is_limited_to_fourteen_days(self):
        self.client.force_login(self.personnel_dispatcher)
        response=self.client.post(reverse('personnel:arrivals'),{
            'workflow_action':'recommendation_add','recommendation_kind':'ARRIVAL',
            'arrival-employees':[self.second_employee.pk],'arrival-camp':self.a.pk,
            'arrival-planned_on':timezone.localdate()+timedelta(days=15),
        })
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'пределах ближайших 14 дней')
        self.assertFalse(MovementRecommendationOverride.objects.exists())
