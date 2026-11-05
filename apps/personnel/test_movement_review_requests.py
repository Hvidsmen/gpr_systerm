from datetime import timedelta
from io import BytesIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.accounts.models import Company, Role, User
from apps.projects.models import ConstructionObject, Project
from apps.resources.models import Employee, Position
from . import housing
from .models import BaseCity, Board, Camp, CampObjectLink, MovementReviewRequest, Person, StaffingGroup, StaffingSlot


class MovementReviewRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Очередь перевахты')
        cls.camp=Camp.objects.create(company=cls.company,name='ВЖГ-1')
        cls.project=Project.objects.create(company=cls.company,name='Проект')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='Нефтепровод')
        cls.site=ConstructionObject.objects.create(company=cls.company,project=cls.project,parent=cls.obj,name='КТП-1')
        CampObjectLink.objects.create(company=cls.company,camp=cls.camp,construction_object=cls.site)
        cls.personnel=User.objects.create_user(username='personnel-request',company=cls.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        cls.reviewer=User.objects.create_user(username='personnel-review',company=cls.company,role=Role.objects.get(code='HR_HEAD'))
        cls.transport=User.objects.create_user(username='transport-no-access',company=cls.company,role=Role.objects.get(code='TRANSPORT_DISPATCHER'))
        cls.position=Position.objects.create(company=cls.company,name='Электромонтёр справочника')
        cls.employee=Employee.objects.create(company=cls.company,last_name='Иванов',first_name='Иван',employee_number='review-1',position=cls.position,phone='+7 900 123-45-67')
        cls.person=Person.objects.create(company=cls.company,employee=cls.employee,full_name=cls.employee.display_name,employee_number=cls.employee.employee_number)
        cls.board=Board.objects.create(company=cls.company,name='ГПР')
        cls.group=StaffingGroup.objects.create(company=cls.company,board=cls.board,position='Начальник участка по ГПР')
        cls.slot=StaffingSlot.objects.create(company=cls.company,group=cls.group,person=cls.person,assigned_from=timezone.localdate())

    def test_personnel_dispatcher_creates_request_and_rotation_accepts_it(self):
        event_count=self.person.housing_events.count()
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        request_row=MovementReviewRequest.objects.get()
        self.assertEqual(request_row.person,self.person)
        self.assertEqual(request_row.kind,'ARRIVAL')
        self.assertIsNone(request_row.event)
        self.assertFalse(self.person.housing_events.exists())
        self.assertEqual(request_row.status,'OPEN')
        self.assertEqual(request_row.planning_position_name,'Начальник участка по ГПР')
        self.assertEqual(request_row.position_name,self.employee.display_position)
        self.assertEqual(request_row.construction_object,self.obj)
        self.assertEqual(request_row.site_object,self.site)
        self.assertEqual(request_row.site,self.site.name)
        self.assertEqual(request_row.phone,self.employee.phone)

        self.client.force_login(self.reviewer)
        dispatcher_page=self.client.get(reverse('personnel:housing'))
        self.assertEqual(dispatcher_page.status_code,200)
        self.assertContains(dispatcher_page,'Состав, назначения и графики')
        self.assertNotContains(dispatcher_page,'Заявки из диспетчерской')
        page=self.client.get(reverse('personnel:movement_reviews'))
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Полученные от диспетчера по персоналу заявки')
        self.assertContains(page,self.person.display_name)
        self.assertContains(page,'Диспетчерская')
        self.assertContains(page,'Перевахта')
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[request_row.pk]))
        self.assertEqual(detail.status_code,200)
        self.assertContains(detail,'Подтвердить заявку')
        self.assertContains(detail,'Должность по ГПР / СМГ')
        self.assertContains(detail,'Должность по справочнику сотрудников')
        self.assertContains(detail,'Выберите проект')
        self.assertContains(detail,'Нефтепровод → КТП-1')
        self.assertContains(detail,'ВЖГ')
        self.assertContains(detail,'Не прибыл')
        changed_date=timezone.localdate()+timedelta(days=2)
        response=self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk]),{
            'review_action':'confirm','employment_kind':'STAFF','planning_position_name':'Начальник участка по ГПР',
            'position_name':'Электромонтёр справочника','construction_object':self.obj.pk,'camp':self.camp.pk,'movement_date':changed_date.isoformat(),
            'base_city_choice':'','new_base_city':'Уфа','phone':'8 900 000-00-00','request_number':'302','travel_time':'08:45',
            'travel_status':'Прибыл','note':'Рейс подтверждён',
        })
        self.assertRedirects(response,reverse('personnel:movement_reviews')+'#movement-requests',fetch_redirect_response=False)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'ACCEPTED')
        self.assertEqual(request_row.reviewed_by,self.reviewer)
        self.assertIsNotNone(request_row.reviewed_at)
        self.assertEqual(request_row.request_number,'302')
        self.assertEqual(request_row.base_city,'Уфа')
        self.assertEqual(request_row.movement_date,changed_date)
        self.assertEqual(request_row.construction_object,self.obj)
        self.assertEqual(request_row.to_camp,self.camp)
        self.assertTrue(BaseCity.objects.filter(company=self.company,name='Уфа').exists())
        self.assertEqual(self.person.housing_events.count(),event_count)
        self.assertIsNone(request_row.event)

        accepted=self.client.get(reverse('personnel:movement_review_accept',args=[request_row.pk]))
        self.assertContains(accepted,'Редактировать')
        edit=self.client.get(reverse('personnel:movement_review_accept',args=[request_row.pk])+'?edit=1')
        self.assertContains(edit,'Сохранить изменения')
        response=self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk]),{
            'review_action':'save','employment_kind':'STAFF','planning_position_name':'Начальник участка по ГПР',
            'position_name':'Электромонтёр справочника','construction_object':self.obj.pk,'camp':self.camp.pk,'movement_date':changed_date.isoformat(),
            'base_city_choice':'','new_base_city':'Уфа','phone':'8 999 111-22-33','request_number':'302','travel_time':'08:45',
            'travel_status':'Прибыл','note':'Данные исправлены',
        })
        self.assertRedirects(response,reverse('personnel:movement_review_accept',args=[request_row.pk]),fetch_redirect_response=False)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'ACCEPTED')
        self.assertEqual(request_row.phone,'8 999 111-22-33')
        self.assertEqual(request_row.note,'Данные исправлены')

    def test_direct_hr_movement_does_not_create_dispatch_request(self):
        housing.move(self.reviewer,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        self.assertFalse(MovementReviewRequest.objects.exists())

    def test_transport_dispatcher_cannot_accept_request(self):
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        request_row=MovementReviewRequest.objects.get()
        self.client.force_login(self.transport)
        self.assertEqual(self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk])).status_code,403)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'OPEN')

    def test_visible_excel_sheets_create_review_requests_without_duplicates(self):
        from openpyxl import Workbook
        book=Workbook();arrival=book.active;arrival.title='Заезд Ноябрь 26'
        arrival.append(['№','штат/новый/перевод','Фамилия Имя Отчество','Должность','Участок','Базовый город','Номер телефона','№ заявки','Дата заезда','Время прилета','Статус'])
        arrival.append([1,'Штат',self.employee.display_name,'Электромонтёр СМГ',self.site.name,'Уфа','8 900 000 00 00',302,timezone.localdate(),'08:45','Ожидается'])
        arrival.append([2,'Новый','Неизвестный Человек','Рабочий',self.site.name,'Омск','',303,timezone.localdate(),'09:00','Ожидается'])
        archive=book.create_sheet('Выезд Январь 24');archive.sheet_state='hidden'
        archive.append(['Фамилия Имя Отчество','Дата выезда']);archive.append([self.employee.display_name,timezone.localdate()])
        stream=BytesIO();book.save(stream);upload=lambda:SimpleUploadedFile('perevakhta.xlsx',stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.client.force_login(self.reviewer)
        response=self.client.post(reverse('personnel:movement_reviews'),{'review_action':'import_excel','movement_excel':upload()})
        self.assertEqual(response.status_code,200);self.assertContains(response,'создано 1');self.assertContains(response,'Неизвестный Человек')
        row=MovementReviewRequest.objects.get();self.assertEqual(row.person,self.person);self.assertEqual(row.request_number,'302');self.assertEqual(row.site_object,self.site);self.assertEqual(row.to_camp,self.camp)
        response=self.client.post(reverse('personnel:movement_reviews'),{'review_action':'import_excel','movement_excel':upload()})
        self.assertEqual(MovementReviewRequest.objects.count(),1);self.assertContains(response,'повторов 1')
