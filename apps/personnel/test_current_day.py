"""Personnel management is a current-day view, including legacy URLs and export."""
from datetime import datetime, timedelta, timezone as dt_timezone
from io import BytesIO
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import openpyxl
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from . import test_housing as fixtures
from .models import StaffingSlot


class CurrentPersonnelDayTests(TestCase):
    setUpTestData=classmethod(fixtures.HousingTests.setUpTestData.__func__)

    def setUp(self):
        self.client.force_login(self.hr);self.today=timezone.localdate();self.url=reverse('personnel:housing')

    def test_past_future_and_invalid_url_dates_cannot_change_current_composition(self):
        for value in (str(self.today-timedelta(days=200)),str(self.today+timedelta(days=200)),'not-a-date'):
            with self.subTest(date=value):
                response=self.client.get(self.url,{'camp':self.a.pk,'date':value})
                self.assertEqual(response.status_code,200);self.assertEqual(response.context['day'],self.today)
                self.assertEqual(response.context['rotation_filter'].cleaned_data['date'],self.today)
                self.assertTrue(response.context['personnel_today_only'])
                self.assertNotContains(response,'name="date"');self.assertNotContains(response,'data-fact-date=')
                self.assertNotContains(response,'data-fact-shortcuts');self.assertContains(response,'Сегодня · '+self.today.strftime('%d.%m.%Y'))

    def test_other_filters_and_calendar_links_keep_current_day_without_date_parameter(self):
        response=self.client.get(self.url,{'camp':self.a.pk,'date':str(self.day),'state':'TRAVEL','q':self.employee.last_name,'roster_view':'groups'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['day'],self.today)
        self.assertEqual(response.context['rotation_stats']['people'],1)
        self.assertEqual(response.context['rows'][0]['today_code'],'ДП')
        self.assertNotIn('date',parse_qs(response.context['roster_query']))
        for item in response.context['roster_view_links']:
            query=parse_qs(urlsplit(item['url']).query);self.assertNotIn('date',query);self.assertEqual(query['state'],['TRAVEL'])
        query=parse_qs(urlsplit(response.context['rotation_calendar_url']).query)
        self.assertNotIn('date',query);self.assertEqual(query['q'],[self.employee.last_name])
        fragment=self.client.get(self.url,{'camp':self.a.pk,'date':str(self.day),'calendar_fragment':'1'})
        self.assertEqual(fragment.context['day'],self.today)

    def test_export_uses_today_even_from_a_legacy_link(self):
        response=self.client.get(reverse('personnel:housing_export'),{'camp':self.a.pk,'date':str(self.day)})
        self.assertEqual(response.status_code,200)
        sheet=openpyxl.load_workbook(BytesIO(response.content)).active
        headings=[cell.value for cell in sheet[1]]
        date_column=headings.index('Дата')+1
        self.assertTrue(all(sheet.cell(row,date_column).value==str(self.today) for row in range(2,sheet.max_row+1)))

    def test_current_day_is_moscow_local_date_not_utc(self):
        instant=datetime(2026,11,4,22,30,tzinfo=dt_timezone.utc)
        with timezone.override('Europe/Moscow'),patch('django.utils.timezone.now',return_value=instant):
            response=self.client.get(self.url,{'camp':self.a.pk,'date':'2020-01-01'})
        self.assertEqual(response.context['day'].isoformat(),'2026-11-05')

    def test_movement_date_and_other_fact_pages_keep_their_date_controls(self):
        response=self.client.get(reverse('personnel:movements'),{'camp':self.a.pk,'date':str(self.day)})
        self.assertEqual(response.context['day'],self.day)
        self.assertEqual(response.context['form'].initial['date'],self.day)
        self.assertContains(response,'name="date"');self.assertContains(response,'data-fact-shortcuts')
        content=render_to_string('core/_fact_context.html',{'day':self.today,'can_edit':True,'save_mode':'auto'})
        self.assertIn('data-fact-shortcuts',content);self.assertIn('>Вчера</button>',content)

    def test_assignment_dates_are_preserved_but_return_opens_current_composition(self):
        from apps.resources.models import Brigade
        role=Brigade.objects.create(company=self.company,name='Начальник участка')
        start=self.day-timedelta(days=15)
        response=self.client.post(reverse('personnel:plan_assignment_create'),{'employee':self.second_employee.pk,'planning_position':role.pk,'construction_object':self.obj.pk,'housing_camp':self.a.pk,'assigned_from':str(start)})
        self.assertEqual(response.status_code,302);self.assertNotIn('date',parse_qs(urlsplit(response.url).query))
        self.assertEqual(StaffingSlot.objects.get(planning_position=role).assigned_from,start)
        page=self.client.get(response.url);self.assertEqual(page.context['day'],self.today)

    def test_invalid_non_date_filter_still_shows_errors_without_date_control(self):
        response=self.client.get(self.url,{'date':'old-link','state':'INVALID'})
        self.assertEqual(response.status_code,400);self.assertNotContains(response,'name="date"',status_code=400)
