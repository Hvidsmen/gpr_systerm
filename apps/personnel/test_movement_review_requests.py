from datetime import timedelta
from io import BytesIO
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError,PermissionDenied

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
        cls.other_personnel=User.objects.create_user(username='other-personnel-request',company=cls.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        cls.reviewer=User.objects.create_user(username='personnel-review',company=cls.company,role=Role.objects.get(code='HR_HEAD'))
        cls.transport=User.objects.create_user(username='transport-no-access',company=cls.company,role=Role.objects.get(code='TRANSPORT_DISPATCHER'))
        cls.admin=User.objects.create_user(username='movement-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
        cls.position=Position.objects.create(company=cls.company,name='Электромонтёр справочника')
        cls.employee=Employee.objects.create(company=cls.company,last_name='Иванов',first_name='Иван',employee_number='review-1',position=cls.position,phone='+7 900 123-45-67')
        cls.person=Person.objects.create(company=cls.company,employee=cls.employee,full_name=cls.employee.display_name,employee_number=cls.employee.employee_number)
        cls.board=Board.objects.create(company=cls.company,name='ГПР')
        cls.group=StaffingGroup.objects.create(company=cls.company,board=cls.board,position='Начальник участка по ГПР')
        cls.slot=StaffingSlot.objects.create(company=cls.company,group=cls.group,person=cls.person,assigned_from=timezone.localdate())

    def complete_trip_details(self,row):
        row.planning_position_name=row.planning_position_name or 'Должность СМГ';row.position_name=row.position_name or 'Должность сотрудника'
        row.construction_object=self.obj;row.base_city='Норильск';row.phone='8 900 000-00-00';row.request_number='123'
        row.travel_time='08:30';row.travel_status='Ожидается';row.employment_kind=row.employment_kind or 'STAFF'
        row.planned_hub_departure_date=timezone.localdate();row.planned_hub_departure_time='12:30'
        row.save(update_fields=['planning_position_name','position_name','construction_object','base_city','phone','request_number','travel_time','travel_status','employment_kind','planned_hub_departure_date','planned_hub_departure_time','updated_at'])

    def test_approver_cannot_execute_perevakhta_or_dispatcher_facts(self):
        from .departure_workflow import arrive_camp,arrive_hub
        manager=User.objects.create_user(username='facts-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get();row.status='PEREVAKHTA';row.tickets_ready=True;row.save(update_fields=['status','tickets_ready','updated_at'])
        self.complete_trip_details(row)
        with self.assertRaises(PermissionDenied):arrive_hub(manager,row,day)
        row.status='EN_ROUTE';row.hub_departed_on=day;row.save(update_fields=['status','hub_departed_on','updated_at'])
        with self.assertRaises(PermissionDenied):arrive_camp(manager,row,day)

    def test_future_approver_cannot_open_request_before_own_stage(self):
        site_chief=User.objects.create_user(username='future-site-chief',company=self.company,role=Role.objects.get(code='SITE_CHIEF'))
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        row=MovementReviewRequest.objects.get();self.client.force_login(site_chief)
        self.assertEqual(self.client.get(reverse('personnel:movement_review_accept',args=[row.pk])).status_code,403)

    def finish_approval(self,row):
        from .departure_workflow import active_route_roles,decide
        self.complete_trip_details(row)
        while active_route_roles(row):
            decide(self.admin,row,True)
            row.refresh_from_db()
        return row

    def test_personnel_dispatcher_creates_request_and_rotation_accepts_it(self):
        event_count=self.person.housing_events.count()
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        request_row=MovementReviewRequest.objects.get()
        automatic_number=request_row.request_number
        self.assertEqual(automatic_number,str(request_row.pk))
        self.assertEqual(request_row.person,self.person)
        self.assertEqual(request_row.kind,'ARRIVAL')
        self.assertIsNone(request_row.event)
        self.assertFalse(self.person.housing_events.exists())
        self.assertEqual(request_row.status,'PENDING_APPROVAL')
        MovementReviewRequest.objects.filter(pk=request_row.pk).update(status='OPEN');request_row.refresh_from_db()
        self.assertEqual(request_row.planning_position_name,'Начальник участка по ГПР')
        self.assertEqual(request_row.position_name,self.employee.display_position)
        self.assertEqual(request_row.construction_object,self.obj)
        self.assertEqual(request_row.site_object,self.site)
        self.assertEqual(request_row.site,self.site.name)
        self.assertEqual(request_row.phone,self.employee.phone)
        self.assertEqual(request_row.travel_status,'Ожидается')

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
        self.assertNotContains(page,'data-personnel-sections')
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[request_row.pk]))
        self.assertEqual(detail.status_code,200)
        self.assertContains(detail,'Подтвердить заявку')
        self.assertContains(detail,'Должность по ГПР / СМГ')
        self.assertContains(detail,'Должность по справочнику сотрудников')
        self.assertContains(detail,'Выберите проект')
        self.assertContains(detail,'Нефтепровод → КТП-1')
        self.assertContains(detail,'ВЖГ')
        self.assertNotContains(detail,'name="travel_status"')
        changed_date=timezone.localdate()+timedelta(days=2)
        response=self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk]),{
            'review_action':'confirm','employment_kind':'STAFF','planning_position_name':'Начальник участка по ГПР',
            'position_name':'Электромонтёр справочника','construction_object':self.obj.pk,'camp':self.camp.pk,'movement_date':changed_date.isoformat(),
            'base_city_choice':'','new_base_city':'Уфа','phone':'8 900 000-00-00','request_number':'302','travel_time':'08:45',
            'planned_hub_departure_date':changed_date.isoformat(),'planned_hub_departure_time':'12:30',
            'travel_status':'Прибыл','note':'Рейс подтверждён',
        })
        self.assertRedirects(response,reverse('personnel:movement_reviews')+'#movement-requests',fetch_redirect_response=False)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'ACCEPTED')
        self.assertEqual(request_row.reviewed_by,self.reviewer)
        self.assertIsNotNone(request_row.reviewed_at)
        self.assertEqual(request_row.request_number,automatic_number)
        self.assertEqual(request_row.base_city,'Уфа')
        self.assertEqual(request_row.movement_date,changed_date)
        self.assertEqual(request_row.construction_object,self.obj)
        self.assertEqual(request_row.to_camp,self.camp)
        self.assertTrue(BaseCity.objects.filter(company=self.company,name='Уфа').exists())
        self.assertEqual(self.person.housing_events.count(),event_count)
        self.assertIsNone(request_row.event)

        accepted=self.client.get(reverse('personnel:movement_review_accept',args=[request_row.pk]))
        self.assertContains(accepted,'Редактировать')
        self.assertContains(accepted,'ОП Дудинка')
        self.assertContains(accepted,'1. Прибытие в ОП Дудинка')
        self.assertContains(accepted,'movement-timeline')
        edit=self.client.get(reverse('personnel:movement_review_accept',args=[request_row.pk])+'?edit=1')
        self.assertContains(edit,'Сохранить изменения')
        response=self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk]),{
            'review_action':'save','employment_kind':'STAFF','planning_position_name':'Начальник участка по ГПР',
            'position_name':'Электромонтёр справочника','construction_object':self.obj.pk,'camp':self.camp.pk,'movement_date':changed_date.isoformat(),
            'base_city_choice':'','new_base_city':'Уфа','phone':'8 999 111-22-33','request_number':'302','travel_time':'08:45',
            'planned_hub_departure_date':changed_date.isoformat(),'planned_hub_departure_time':'12:30',
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

    def test_admin_personnel_sections_are_in_sidebar_not_page_tabs(self):
        self.client.force_login(self.admin);page=self.client.get(reverse('personnel:housing'))
        self.assertContains(page,'Диспетчерская');self.assertContains(page,'Перевахта');self.assertContains(page,'Магазин подбора')
        self.assertNotContains(page,'data-personnel-sections')

    def test_request_is_cancelled_with_reason_and_admin_can_delete_it(self):
        from apps.planning.models import WorkTask
        housing.move(self.reviewer,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        housing.request_moves(self.personnel,'DEPARTURE',self.camp,timezone.localdate(),[self.person],departure_destination='REST')
        row=MovementReviewRequest.objects.get()
        self.client.force_login(self.reviewer)
        response=self.client.post(reverse('personnel:movement_review_cancel',args=[row.pk]),{'reason':'Создана ошибочно'})
        self.assertRedirects(response,reverse('personnel:movement_review_accept',args=[row.pk]),fetch_redirect_response=False)
        row.refresh_from_db();self.assertEqual(row.status,'CANCELLED');self.assertEqual(row.cancellation_reason,'Создана ошибочно')
        self.assertEqual(row.cancelled_by,self.reviewer);self.assertIsNotNone(row.cancelled_at)
        self.assertEqual(WorkTask.objects.get(key=f'personnel-movement-approval:{row.pk}').status,'SUPERSEDED')
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(detail,'Заявка отменена');self.assertNotContains(detail,'Редактировать данные заявки')
        self.client.force_login(self.admin)
        response=self.client.post(reverse('personnel:movement_review_delete',args=[row.pk]))
        self.assertRedirects(response,reverse('personnel:movement_reviews')+'#movement-requests',fetch_redirect_response=False)
        self.assertFalse(MovementReviewRequest.objects.filter(pk=row.pk).exists())

    def test_request_cannot_be_cancelled_after_actual_route_started(self):
        from .departure_workflow import arrive_hub,decide
        manager=User.objects.create_user(username='cancel-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        day=timezone.localdate();housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person])
        housing.request_moves(self.personnel,'DEPARTURE',self.camp,day,[self.person],departure_destination='REST')
        row=MovementReviewRequest.objects.get(kind='DEPARTURE');self.finish_approval(row);arrive_hub(self.reviewer,row,day)
        self.client.force_login(self.reviewer)
        self.client.post(reverse('personnel:movement_review_cancel',args=[row.pk]),{'reason':'Попытка отмены'})
        row.refresh_from_db();self.assertEqual(row.status,'AT_HUB')

    def test_dispatcher_can_cancel_own_request_from_my_requests(self):
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get();self.client.force_login(self.personnel)
        active=self.client.get(reverse('personnel:my_movement_requests'))
        self.assertContains(active,'Отменить');self.assertNotContains(active,'>Удалить<')
        cancelled=self.client.post(reverse('personnel:movement_review_cancel',args=[row.pk]),
            {'reason':'Поездка больше не требуется','next':reverse('personnel:my_movement_requests')+'?scope=history'})
        self.assertRedirects(cancelled,reverse('personnel:my_movement_requests')+'?scope=history',fetch_redirect_response=False)
        history=self.client.get(reverse('personnel:my_movement_requests'),{'scope':'history'})
        self.assertContains(history,'Поездка больше не требуется');self.assertNotContains(history,'>Удалить<')
        self.assertTrue(MovementReviewRequest.objects.filter(pk=row.pk,status='CANCELLED').exists())

    def test_completed_requests_use_one_paginated_table(self):
        day=timezone.localdate()
        MovementReviewRequest.objects.bulk_create([
            MovementReviewRequest(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
                to_camp=self.camp,requested_by=self.personnel,status='COMPLETED',request_number=f'history-{number}')
            for number in range(51)
        ])
        self.client.force_login(self.personnel)
        first=self.client.get(reverse('personnel:my_movement_requests'),{'scope':'history'})
        self.assertContains(first,'Завершённые заявки',count=1)
        self.assertContains(first,'Всего записей: 51')
        self.assertEqual(len(first.context['history_page']),50)
        self.assertContains(first,'page=2')
        second=self.client.get(reverse('personnel:my_movement_requests'),{'scope':'history','page':2})
        self.assertEqual(len(second.context['history_page']),1)
        self.assertContains(second,'Назад')

    def test_dispatcher_cannot_cancel_after_first_approval(self):
        from .departure_workflow import decide
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get();manager=User.objects.create_user(username='first-approval-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        decide(manager,row,True,'Первый этап согласован');row.refresh_from_db();self.assertTrue(row.has_approved_stage())
        self.client.force_login(self.personnel);page=self.client.get(reverse('personnel:my_movement_requests'))
        self.assertNotContains(page,'>Отменить<')
        response=self.client.post(reverse('personnel:movement_review_cancel',args=[row.pk]),{'reason':'Передумали'})
        self.assertEqual(response.status_code,302);row.refresh_from_db();self.assertEqual(row.status,'PENDING_APPROVAL')
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]));self.assertNotContains(detail,'Отменить заявку')

    def test_batch_request_creates_one_task_and_supports_individual_and_bulk_decisions(self):
        from apps.planning.models import WorkTask
        second_employee=Employee.objects.create(company=self.company,last_name='Петров',first_name='Пётр',employee_number='review-2',position=self.position)
        second=Person.objects.create(company=self.company,employee=second_employee,full_name=second_employee.display_name,employee_number=second_employee.employee_number)
        day=timezone.localdate();housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person,second])
        batch=uuid4();housing.request_moves(self.personnel,'DEPARTURE',self.camp,day,[self.person,second],departure_destination='REST',approval_batch=batch)
        rows=list(MovementReviewRequest.objects.filter(approval_batch=batch).order_by('pk'));self.assertEqual(len(rows),2)
        rows[1].departure_destination='DISMISSAL';rows[1].save(update_fields=['departure_destination','updated_at'])
        task=WorkTask.objects.get(key=f'personnel-movement-batch:{batch}')
        self.assertIn('2 сотрудников',task.title)
        self.client.force_login(self.personnel);mine=self.client.get(reverse('personnel:my_movement_requests'))
        self.assertContains(mine,'Мои заявки');self.assertContains(mine,self.person.display_name);self.assertContains(mine,second.display_name);self.assertContains(mine,'На согласовании')
        self.assertEqual(self.client.get(reverse('personnel:movement_reviews')).status_code,403)
        self.client.force_login(self.admin);url=reverse('planning:task_detail',args=[task.pk])
        page=self.client.get(url);self.assertContains(page,self.person.display_name);self.assertContains(page,second.display_name)
        self.assertContains(page,'Причина выезда');self.assertContains(page,'Межвахтовый отдых');self.assertContains(page,'Увольнение')
        self.assertContains(page,'Согласовать выбранных');self.assertContains(page,'Не согласовать всех ожидающих')
        self.client.post(url,{'action':'movement_approve_selected','selected':[rows[0].pk]})
        rows[0].refresh_from_db();rows[1].refresh_from_db();task.refresh_from_db()
        self.assertEqual(rows[0].status,'PENDING_APPROVAL');self.assertEqual(rows[1].status,'PENDING_APPROVAL');self.assertEqual(task.status,'OPEN')
        self.client.post(url,{'action':'movement_reject_all','text':'Перенести дату'})
        rows[0].refresh_from_db();rows[1].refresh_from_db();task.refresh_from_db()
        self.assertEqual(rows[0].status,'CANCELLED');self.assertEqual(rows[1].status,'CANCELLED')
        self.assertEqual(rows[1].approval_comment,'Перенести дату');self.assertIn('Отменено согласующим',rows[1].cancellation_reason)
        self.assertEqual(task.status,'SUPERSEDED')

    def test_perevakhta_batch_shows_trip_data_state_and_blocks_incomplete_row(self):
        from apps.planning.models import WorkTask
        from .departure_workflow import decide
        day=timezone.localdate();housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person])
        batch=uuid4();housing.request_moves(self.personnel,'DEPARTURE',self.camp,day,[self.person],departure_destination='REST',approval_batch=batch)
        row=MovementReviewRequest.objects.get(approval_batch=batch)
        from .forms import MovementReviewForm
        form=MovementReviewForm(instance=row,confirm=True)
        self.assertTrue(form.fields['request_number'].disabled)
        self.assertFalse(form.fields['travel_time'].required)
        self.assertFalse(form.fields['planned_hub_departure_time'].required)
        self.assertNotIn('travel_status',form.fields)
        row.travel_status='';row.save(update_fields=['travel_status','updated_at'])
        decide(self.admin,row,True);row.refresh_from_db()
        decide(self.admin,row,True);row.refresh_from_db()
        task=WorkTask.objects.get(key=f'personnel-movement-batch:{batch}')
        self.client.force_login(self.admin);url=reverse('planning:task_detail',args=[task.pk])
        page=self.client.get(url)
        self.assertNotContains(page,'data-task-description')
        self.assertContains(page,'Данные поездки не заполнены')
        self.assertContains(page,'Открыть и заполнить заявку')
        checkbox=f'<input type="checkbox" name="selected" value="{row.pk}" data-movement-row'
        self.assertNotContains(page,checkbox)
        detail_url=reverse('personnel:movement_review_accept',args=[row.pk])
        incomplete=self.client.post(detail_url,{'review_action':'complete_details'})
        self.assertEqual(incomplete.status_code,200)
        self.assertContains(incomplete,'Обязательное поле')
        saved=self.client.post(detail_url,{
            'review_action':'complete_details','planning_position_name':'Должность СМГ','position_name':'Должность сотрудника',
            'construction_object':self.obj.pk,'camp':self.camp.pk,'movement_date':day.isoformat(),
            'departure_destination':'REST',
            'base_city_choice':'','new_base_city':'Норильск','phone':'8 900 000-00-00',
            'planned_hub_departure_date':day.isoformat(),'travel_status':'Ожидается','note':'',
        })
        self.assertRedirects(saved,url,fetch_redirect_response=False)
        row.refresh_from_db();self.assertIsNone(row.travel_time);self.assertIsNone(row.planned_hub_departure_time)
        page=self.client.get(url)
        self.assertContains(page,'Данные поездки заполнены')
        self.assertContains(page,'Открыть заявку')
        self.assertContains(page,checkbox)

    def test_arrival_and_transfer_batches_also_create_single_approval_task(self):
        from apps.planning.models import WorkTask
        second_employee=Employee.objects.create(company=self.company,last_name='Сидоров',first_name='Семён',employee_number='review-3',position=self.position)
        second=Person.objects.create(company=self.company,employee=second_employee,full_name=second_employee.display_name,employee_number=second_employee.employee_number)
        day=timezone.localdate();arrival_batch=uuid4()
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person,second],approval_batch=arrival_batch)
        self.assertEqual(WorkTask.objects.filter(key=f'personnel-movement-batch:{arrival_batch}').count(),1)
        self.assertEqual(MovementReviewRequest.objects.filter(approval_batch=arrival_batch,status='PENDING_APPROVAL').count(),2)
        MovementReviewRequest.objects.filter(approval_batch=arrival_batch).delete();WorkTask.objects.filter(key=f'personnel-movement-batch:{arrival_batch}').delete()
        target=Camp.objects.create(company=self.company,name='ВЖГ-2');housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person,second])
        transfer_batch=uuid4();housing.request_moves(self.personnel,'TRANSFER',self.camp,day,[self.person,second],target=target,approval_batch=transfer_batch)
        task=WorkTask.objects.get(key=f'personnel-movement-batch:{transfer_batch}');self.assertEqual(MovementReviewRequest.objects.filter(approval_batch=transfer_batch,status='PENDING_APPROVAL').count(),2)
        for row in MovementReviewRequest.objects.filter(approval_batch=transfer_batch):self.finish_approval(row)
        self.assertEqual(set(MovementReviewRequest.objects.filter(approval_batch=transfer_batch).values_list('status',flat=True)),{'OPEN'})

    def test_dispatcher_sees_only_own_request_details(self):
        row=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=timezone.localdate(),to_camp=self.camp,requested_by=self.other_personnel,status='PENDING_APPROVAL')
        self.client.force_login(self.personnel)
        self.assertEqual(self.client.get(reverse('personnel:movement_review_accept',args=[row.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('personnel:my_movement_requests')),self.person.display_name)

    def test_trip_dates_and_times_are_grouped_by_route_stage(self):
        day=timezone.localdate();housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person])
        housing.request_moves(self.personnel,'DEPARTURE',self.camp,day,[self.person],departure_destination='REST')
        row=MovementReviewRequest.objects.get(kind='DEPARTURE');self.client.force_login(self.admin)
        content=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk])+'?edit=1').content.decode()
        self.assertIn('1. Выезд из ВЖГ',content);self.assertIn('2. Выезд из ОП Дудинка',content)
        self.assertLess(content.index('name="movement_date"'),content.index('name="travel_time"'))
        self.assertLess(content.index('name="travel_time"'),content.index('name="planned_hub_departure_date"'))
        self.assertLess(content.index('name="planned_hub_departure_date"'),content.index('name="planned_hub_departure_time"'))

    def test_empty_existing_request_prefills_phone_from_employee_directory(self):
        row=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=timezone.localdate(),to_camp=self.camp,requested_by=self.personnel,phone='')
        self.client.force_login(self.admin)
        page=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk])+'?edit=1')
        self.assertContains(page,f'value="{self.employee.phone}"')

    def test_request_list_shows_workflow_stage_instead_of_legacy_travel_status(self):
        row=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',movement_date=timezone.localdate(),
            from_camp=self.camp,requested_by=self.personnel,status='PEREVAKHTA',travel_status='Ожидается')
        self.assertEqual(row.workflow_status_label(),'Ожидается прибытие в ОП Дудинка')
        self.client.force_login(self.reviewer)
        page=self.client.get(reverse('personnel:movement_reviews')+'?kind=departure')
        self.assertContains(page,'Ожидается прибытие в ОП Дудинка')
        self.assertNotContains(page,'>Ожидается</span>')
        self.client.force_login(self.personnel);mine=self.client.get(reverse('personnel:my_movement_requests'))
        self.assertContains(mine,'Ожидается прибытие в ОП Дудинка');self.assertNotContains(mine,'<td>Ожидается</td>')
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',movement_date=timezone.localdate(),
            from_camp=self.camp,requested_by=self.personnel,status='AT_HUB',travel_status='Ожидается')
        filtered=self.client.get(reverse('personnel:my_movement_requests'),{'stage':'DUDINKA_ARRIVAL'})
        filtered_ids=[item.pk for group in filtered.context['groups'] for item in group['items']]
        self.assertEqual(filtered_ids,[row.pk]);self.assertContains(filtered,'<option value="DUDINKA_ARRIVAL" selected>')

    def test_personnel_phone_backfills_empty_employee_directory_phone(self):
        employee=Employee.objects.create(company=self.company,last_name='Телефонов',first_name='Пётр',employee_number='phone-sync',position=self.position,phone='')
        person=Person.objects.create(company=self.company,employee=employee,full_name=employee.display_name,employee_number=employee.employee_number,phone='+7 999 123-45-67')
        employee.refresh_from_db();self.assertEqual(employee.phone,person.phone)

    def test_departure_goes_through_manager_dudinka_and_parallel_documents(self):
        from apps.planning.models import WorkTask
        from .departure_workflow import arrive_hub,decide,leave_hub,update_hub
        manager=User.objects.create_user(username='project-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        day=timezone.localdate()
        housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person])
        housing.request_moves(self.personnel,'DEPARTURE',self.camp,day,[self.person],departure_destination='REST')
        row=MovementReviewRequest.objects.get(kind='DEPARTURE')
        self.assertEqual(row.status,'PENDING_APPROVAL')
        task=WorkTask.objects.get(key=f'personnel-movement-approval:{row.pk}')
        self.assertEqual(task.target_role,'MANAGER')
        decide(manager,row,True,'Согласовано');row.refresh_from_db()
        decide(self.admin,row,True);row.refresh_from_db()
        with self.assertRaisesMessage(ValidationError,'Сначала заполните обязательные первичные данные'):decide(self.admin,row,True)
        self.finish_approval(row)
        self.assertEqual(row.status,'PEREVAKHTA')
        arrive_hub(self.reviewer,row,day)
        row.refresh_from_db();self.assertEqual(row.status,'AT_HUB')
        self.assertEqual(self.person.stays.get().departed_on,day)
        self.client.force_login(self.reviewer)
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(detail,'Плановые и фактические даты маршрута')
        self.assertContains(detail,'1. Выезд из ВЖГ')
        self.assertContains(detail,'2. ОП Дудинка')
        self.assertContains(detail,day.strftime('%d.%m.%Y'))
        update_hub(self.reviewer,row,tickets_ready=True)
        update_hub(self.reviewer,row,clearance_document=SimpleUploadedFile('clearance.pdf',b'pdf'))
        row.refresh_from_db();self.assertTrue(row.tickets_ready);self.assertTrue(row.clearance_document)
        download=self.client.get(reverse('personnel:movement_review_clearance',args=[row.pk]))
        self.assertEqual(download.status_code,200);self.assertEqual(b''.join(download.streaming_content),b'pdf')
        self.assertIn('attachment;',download['Content-Disposition'])
        leave_hub(self.reviewer,row,day)
        row.refresh_from_db();self.assertEqual(row.status,'COMPLETED');self.assertEqual(row.hub_departed_on,day)
        self.assertGreaterEqual(row.workflow_events.count(),14)

    def test_future_dates_are_rejected_for_actual_route_steps(self):
        from .departure_workflow import arrive_camp,arrive_hub,leave_hub,update_hub
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get(kind='ARRIVAL');self.finish_approval(row)
        hub_arrival=day+timedelta(days=2);hub_departure=day+timedelta(days=3);camp_arrival=day+timedelta(days=4)
        row.planned_hub_departure_date=hub_departure;row.save(update_fields=['planned_hub_departure_date','updated_at'])
        with self.assertRaisesMessage(ValidationError,'не может быть в будущем'):arrive_hub(self.reviewer,row,hub_arrival)
        update_hub(self.reviewer,row,tickets_ready=True,planned_hub_arrival_date=hub_arrival)
        with self.assertRaisesMessage(ValidationError,'после прибытия сотрудника'):update_hub(self.reviewer,row,clearance_document=SimpleUploadedFile('too-early.pdf',b'pdf'))
        with self.assertRaisesMessage(ValidationError,'не может быть в будущем'):arrive_hub(self.reviewer,row,hub_arrival)
        row.status='AT_HUB';row.hub_arrived_on=day;row.tickets_ready=True
        row.clearance_document=SimpleUploadedFile('clearance.pdf',b'pdf');row.save()
        with self.assertRaisesMessage(ValidationError,'не может быть в будущем'):leave_hub(self.reviewer,row,hub_departure)
        row.status='EN_ROUTE';row.hub_departed_on=day;row.save(update_fields=['status','hub_departed_on','updated_at'])
        with self.assertRaisesMessage(ValidationError,'не может быть в будущем'):arrive_camp(self.reviewer,row,camp_arrival)

    def test_ticket_purchase_can_change_planned_dudinka_arrival(self):
        from .departure_workflow import update_hub
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get(kind='ARRIVAL');self.finish_approval(row)
        changed=day+timedelta(days=2)
        row.planned_hub_departure_date=day+timedelta(days=3);row.save(update_fields=['planned_hub_departure_date','updated_at'])
        self.client.force_login(self.reviewer);page=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(page,'name="planned_hub_arrival_date"');self.assertContains(page,'Дата прибытия в ОП Дудинка по билету')
        self.assertNotContains(page,f'value="{day.isoformat()}"')
        update_hub(self.reviewer,row,tickets_ready=True,planned_hub_arrival_date=changed)
        row.refresh_from_db();self.assertEqual(row.movement_date,changed);self.assertTrue(row.tickets_ready)
        row.tickets_ready=False;row.save(update_fields=['tickets_ready','updated_at'])
        with self.assertRaisesMessage(ValidationError,'не может быть позже планового выезда'):
            update_hub(self.reviewer,row,tickets_ready=True,planned_hub_arrival_date=day+timedelta(days=4))

    def test_parallel_rejection_cancels_person_and_preserves_approval_history(self):
        from .departure_workflow import active_route_roles,decide,resubmit
        day=timezone.localdate();batch=uuid4();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person],approval_batch=batch)
        row=MovementReviewRequest.objects.get();self.complete_trip_details(row)
        for code in ['MANAGER','SITE_CHIEF','PEREVAKHTA_SPECIALIST']:
            user=User.objects.create_user(username='route-'+code.lower(),company=self.company,role=Role.objects.get(code=code))
            decide(user,row,True);row.refresh_from_db()
        approved=User.objects.create_user(username='route-mechanic',company=self.company,role=Role.objects.get(code='CHIEF_MECHANIC'))
        returned=User.objects.create_user(username='route-paramedic',company=self.company,role=Role.objects.get(code='PARAMEDIC'))
        decide(approved,row,True);row.refresh_from_db();decide(returned,row,False,'Нужны медицинские данные');row.refresh_from_db()
        self.assertEqual(row.status,'CANCELLED');self.assertEqual(row.approval_route['CHIEF_MECHANIC']['status'],'APPROVED')
        self.assertEqual(row.approval_route['PARAMEDIC']['status'],'CANCELLED');self.assertEqual(row.cancelled_by,returned)
        self.assertIn('Фельдшер',row.cancellation_reason);self.assertIn(returned.full_name or returned.username,row.cancellation_reason)
        self.assertIn('Нужны медицинские данные',row.cancellation_reason);self.assertEqual(active_route_roles(row),set())
        self.client.force_login(self.personnel);history=self.client.get(reverse('personnel:my_movement_requests'),{'scope':'history'})
        self.assertContains(history,'Отменено согласующим');self.assertContains(history,'Фельдшер');self.assertContains(history,'Нужны медицинские данные')
        with self.assertRaisesMessage(ValidationError,'только возвращённую'):resubmit(self.personnel,row)

    def test_arrival_is_recorded_only_after_dudinka_and_reaching_camp(self):
        from .departure_workflow import arrive_camp,arrive_hub,decide,leave_hub,update_hub
        manager=User.objects.create_user(username='arrival-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        day=timezone.localdate()
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get(kind='ARRIVAL')
        self.assertEqual(row.status,'PENDING_APPROVAL');self.assertFalse(self.person.stays.exists())
        self.finish_approval(row);update_hub(self.reviewer,row,tickets_ready=True,planned_hub_arrival_date=day);arrive_hub(self.reviewer,row,day);row.refresh_from_db()
        self.assertEqual(row.status,'AT_HUB');self.assertFalse(self.person.stays.exists())
        update_hub(self.reviewer,row,clearance_document=SimpleUploadedFile('arrival-clearance.pdf',b'pdf'))
        leave_hub(self.reviewer,row,day);row.refresh_from_db()
        self.assertEqual(row.status,'EN_ROUTE');self.assertFalse(self.person.stays.exists())
        arrive_camp(self.reviewer,row,day);row.refresh_from_db()
        self.assertEqual(row.status,'COMPLETED');self.assertEqual(self.person.stays.get().camp,self.camp)

    def test_new_employee_is_registered_in_zup_stage_at_dudinka(self):
        from .departure_workflow import leave_hub,register_new_employee
        from .models import ZupWorkSchedule
        person=Person.objects.create(company=self.company,full_name='Новый Работник Сергеевич',phone='89990001122')
        row=MovementReviewRequest.objects.create(company=self.company,person=person,kind='ARRIVAL',movement_date=timezone.localdate(),
            to_camp=self.camp,requested_by=self.personnel,status='AT_HUB',employment_kind='NEW',phone=person.phone,
            hub_arrived_on=timezone.localdate(),tickets_ready=True)
        schedule=ZupWorkSchedule.objects.create(company=self.company,name='Вахта 60/30',is_active=True)
        specialist=User.objects.create_user(username='new-employee-specialist',company=self.company,role=Role.objects.get(code='PERSONNEL_SPECIALIST'))
        self.client.force_login(specialist);page=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(page,'Оформление нового сотрудника в ЗУП');self.assertContains(page,'Табельный номер ЗУП')
        with self.assertRaisesMessage(ValidationError,'завершите оформление'):leave_hub(self.admin,row,timezone.localdate())
        employee=register_new_employee(specialist,row,employee_number='NEW-001',position=self.position,schedule=schedule,phone='89990001122')
        person.refresh_from_db();row.refresh_from_db();self.assertEqual(person.employee,employee);self.assertEqual(employee.employee_number,'NEW-001')
        self.assertEqual(employee.zup_card['schedule'],'Вахта 60/30');self.assertEqual(row.position_name,self.position.name)

    def test_planning_position_falls_back_to_employee_position(self):
        self.slot.cancelled_at=timezone.now();self.slot.save(update_fields=['cancelled_at','updated_at'])
        self.assertEqual(housing.planning_position_name(self.person,timezone.localdate()),self.employee.display_position)

    def test_new_request_screen_creates_review_request_for_hr_role(self):
        self.client.force_login(self.reviewer);url=reverse('personnel:movements');day=timezone.localdate()
        page=self.client.get(url,{'camp':self.camp.pk});snapshot=page.context['form'].initial['snapshot']
        response=self.client.post(url,{'workflow_action':'move','camp':self.camp.pk,'kind':'ARRIVAL','date':day,
            'employees':[self.employee.pk],'snapshot':snapshot})
        self.assertEqual(response.status_code,302);self.assertFalse(self.person.stays.exists())
        row=MovementReviewRequest.objects.get();self.assertEqual(row.requested_by,self.reviewer);self.assertEqual(row.source,'DISPATCHER')

    def test_transport_dispatcher_cannot_accept_request(self):
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        request_row=MovementReviewRequest.objects.get()
        self.client.force_login(self.transport)
        self.assertEqual(self.client.post(reverse('personnel:movement_review_accept',args=[request_row.pk])).status_code,403)
        request_row.refresh_from_db()
        self.assertEqual(request_row.status,'PENDING_APPROVAL')

    def test_review_tabs_and_date_filter_separate_arrivals_from_departures(self):
        first=timezone.localdate();second=first+timedelta(days=1)
        arrival=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=first,to_camp=self.camp,requested_by=self.personnel)
        departure=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',movement_date=second,from_camp=self.camp,requested_by=self.personnel)
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_reviews')
        page=self.client.get(url)
        self.assertContains(page,'Заезд');self.assertContains(page,'Выезд и увольнение');self.assertContains(page,'Дата заявки')
        self.assertContains(page,f'housing/movement-requests/{arrival.pk}/');self.assertNotContains(page,f'housing/movement-requests/{departure.pk}/')
        page=self.client.get(url,{'kind':'departure','date':second.isoformat()})
        self.assertContains(page,f'housing/movement-requests/{departure.pk}/');self.assertNotContains(page,f'housing/movement-requests/{arrival.pk}/')
        page=self.client.get(url,{'kind':'departure','date':first.isoformat()})
        self.assertNotContains(page,f'housing/movement-requests/{departure.pk}/')

    def test_dispatcher_and_excel_requests_are_separate_and_newest_first(self):
        day=timezone.localdate()
        older=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,to_camp=self.camp,requested_by=self.personnel)
        imported=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day+timedelta(days=1),to_camp=self.camp,requested_by=self.reviewer,source='EXCEL')
        newer=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day+timedelta(days=2),to_camp=self.camp,requested_by=self.personnel)
        self.client.force_login(self.reviewer);page=self.client.get(reverse('personnel:movement_reviews'))
        self.assertContains(page,'Заявки с портала');self.assertContains(page,'Заявки из Excel')
        self.assertEqual([row.pk for row in page.context['dispatcher_review_requests']],[newer.pk,older.pk])
        self.assertEqual([row.pk for row in page.context['excel_review_requests']],[imported.pk])
        sorted_page=self.client.get(reverse('personnel:movement_reviews'),{'sort':'movement_asc'})
        self.assertEqual(sorted_page.context['review_sort'],'movement_asc');self.assertEqual([row.pk for row in sorted_page.context['dispatcher_review_requests']],[older.pk,newer.pk])
        self.assertContains(sorted_page,'Дата заезда ↑');self.assertContains(sorted_page,'Создана')

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
        row=MovementReviewRequest.objects.get();self.assertEqual(row.person,self.person);self.assertEqual(row.source,'EXCEL');self.assertEqual(row.request_number,'302');self.assertEqual(row.site_object,self.site);self.assertEqual(row.to_camp,self.camp)
        response=self.client.post(reverse('personnel:movement_reviews'),{'review_action':'import_excel','movement_excel':upload()})
        self.assertEqual(MovementReviewRequest.objects.count(),1);self.assertContains(response,'повторов 1')
