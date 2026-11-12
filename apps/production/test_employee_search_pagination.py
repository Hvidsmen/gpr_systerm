from uuid import uuid4
from django.test import TestCase
from django.urls import reverse

from apps.resources.models import Employee
from apps.works.models import ProjectWork
from apps.planning.models import DailyScheduleEntry
from .dispatch_models import EmployeeActivity
from .models import DailyFact
from . import test_dispatch as fixtures


class ProductionEmployeeSearchPaginationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.DispatcherTests.setUpTestData.__func__(cls)
        employees=[Employee(company=cls.company,last_name=f'Массовый{index:03}',first_name='Сотрудник',employee_number=f'T-{index:03}',position=cls.employee.position) for index in range(205)]
        Employee.objects.bulk_create(employees)
        EmployeeActivity.objects.bulk_create([EmployeeActivity(company=cls.company,construction_object=cls.obj,date=cls.day,employee=employee,position=employee.position,entry_direction='works',reported_by=cls.users['DISPATCHER']) for employee in employees])
        works=[ProjectWork(company=cls.company,section=cls.work.section,code=f'MASS-{index:03}',name=f'Массовая работа {index:03}',unit='m') for index in range(204)]
        ProjectWork.objects.bulk_create(works)
        DailyScheduleEntry.objects.bulk_create([DailyScheduleEntry(schedule=cls.schedule,key=f'work:{work.pk}',kind='work',label=work.name,metadata={'id':work.pk,'kind':'SIMPLE'},rows=[{'date':str(cls.day),'item_id':None,'quantity':'1'}]) for work in works])

    def setUp(self):self.client.force_login(self.users['DISPATCHER'])

    def page(self,number=1):
        return self.client.get(reverse('production:fact_day_workspace'),{'construction_object':self.obj.pk,'date':self.day,'section':'works','people_page':number,'entry_step':2})

    def test_daily_people_are_rendered_in_server_pages_of_one_hundred(self):
        first=self.page();self.assertEqual(first.status_code,200)
        labor=next(group for group in first.context['groups'] if group['prefix']=='labor')
        self.assertEqual(labor['page'].paginator.per_page,100)
        self.assertEqual(labor['page'].paginator.count,205)
        self.assertEqual(len([form for form in labor['formset'] if form.instance.pk]),100)
        self.assertLessEqual(len(first.context['production_employee_locations']),100)
        third=self.page(3);labor=next(group for group in third.context['groups'] if group['prefix']=='labor')
        self.assertEqual(len([form for form in labor['formset'] if form.instance.pk]),5)
        self.assertContains(third,'Страница 3 из 3')

    def test_employee_search_returns_fifty_rows_and_filters_on_server(self):
        url=reverse('production:production_employee_search')
        response=self.client.get(url,{'construction_object':self.obj.pk,'date':self.day,'q':'Массовый','page':1})
        self.assertEqual(response.status_code,200,response.content);result=response.json()
        self.assertEqual(len(result['items']),50);self.assertEqual(result['total'],205);self.assertTrue(result['has_next'])
        exact=self.client.get(url,{'construction_object':self.obj.pk,'date':self.day,'q':'T-204'}).json()
        self.assertEqual([item['number'] for item in exact['items']],['T-204'])

    def test_daily_works_are_rendered_in_server_pages_of_one_hundred(self):
        first=self.client.get(reverse('production:fact_day_workspace'),{'construction_object':self.obj.pk,'date':self.day,'section':'works'})
        self.assertEqual(first.status_code,200)
        works=next(group for group in first.context['groups'] if group['prefix']=='works')
        self.assertEqual(first.context['work_page'].paginator.per_page,100)
        self.assertEqual(first.context['work_page'].paginator.count,205)
        self.assertEqual(len(works['formset']),100)
        third=self.client.get(reverse('production:fact_day_workspace'),{'construction_object':self.obj.pk,'date':self.day,'section':'works','work_page':3})
        works=next(group for group in third.context['groups'] if group['prefix']=='works')
        self.assertEqual(len(works['formset']),5)
        self.assertContains(third,'Страница 3 из 3')

    def test_new_saved_rows_can_be_focused_on_their_server_page(self):
        person=EmployeeActivity.objects.order_by('pk').last()
        people=self.client.get(reverse('production:fact_day_workspace'),{'construction_object':self.obj.pk,'date':self.day,'section':'works','focus_person':person.pk})
        self.assertEqual(people.context['people_page'].number,3)
        works=list(ProjectWork.objects.filter(section=self.work.section).order_by('pk')[:101])
        DailyFact.objects.bulk_create([DailyFact(company=self.company,date=self.day,project_work=work,actual_quantity=1,reported_by=self.users['DISPATCHER']) for work in works])
        focus=DailyFact.objects.order_by('pk').last()
        page=self.client.get(reverse('production:fact_day_workspace'),{'construction_object':self.obj.pk,'date':self.day,'section':'works','focus_work':focus.pk})
        self.assertEqual(page.context['work_page'].number,2)
        formset=next(group for group in page.context['groups'] if group['prefix']=='works')['formset']
        self.assertTrue(any(form.instance.pk==focus.pk for form in formset))

    def test_search_rejects_an_object_outside_user_scope(self):
        response=self.client.get(reverse('production:production_employee_search'),{'construction_object':self.other.pk,'date':self.day})
        self.assertEqual(response.status_code,400)

    def test_live_sync_returns_only_open_page(self):
        opened=self.page(2);session=str(uuid4())
        response=self.client.post(reverse('production:production_live'),{
            'actor_id':self.users['DISPATCHER'].pk,'company_id':self.company.pk,'construction_object':self.obj.pk,
            'date':str(self.day),'session':session,'protocol':'compact-v1','step':2,'reset_epoch':0,
            'production_snapshot':opened.context['production_snapshot'],'people_page':2,'people_page_size':100,
        },content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(len(response.json()['rows']['labor']),100)
