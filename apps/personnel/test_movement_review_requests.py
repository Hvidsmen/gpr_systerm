from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from uuid import uuid4

from django.core import mail
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError,PermissionDenied

from apps.accounts.approval_assignments import replace
from apps.accounts.invitation_models import CompanyMailSettings
from apps.accounts.models import ApprovalNotice, Company, Role, User
from apps.planning.models import WorkTask
from apps.projects.models import ConstructionObject, Project
from apps.resources.models import Employee, Position
from . import housing
from .models import BaseCity, Board, Camp, CampObjectLink, MovementExcelSiteMapping, MovementReviewRequest, Person, StaffingGroup, StaffingSlot


class MovementReviewRequestTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Очередь перевахты')
        cls.camp=Camp.objects.create(company=cls.company,name='Местонахождение-1')
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

    @override_settings(STAGING_MODE=True,EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        PORTAL_PUBLIC_URL='http://192.168.70.9:8002/')
    def test_initial_submission_sends_approval_mail_and_opening_backfills_missing_notice(self):
        self.admin.email='movement-admin@example.org';self.admin.save(update_fields=['email'])
        CompanyMailSettings.objects.create(company=self.company,host='smtp.example.org',port=25,security='NONE',
            from_email='portal@example.org',portal_url='http://portal.example.org/')
        manager=User.objects.create_user(username='mail-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        site_chief=User.objects.create_user(username='mail-site-chief',company=self.company,role=Role.objects.get(code='SITE_CHIEF'))
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=manager,actor=self.admin,
            construction_object=self.obj,camp=self.camp)
        replace(company=self.company,process='MOVEMENT',stage_code='SITE_CHIEF',user=site_chief,actor=self.admin,
            construction_object=self.obj,camp=self.camp)

        with self.captureOnCommitCallbacks(execute=True):
            housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])

        notices=ApprovalNotice.objects.filter(company=self.company,process='MOVEMENT').order_by('stage_code')
        self.assertEqual(notices.count(),2)
        self.assertEqual(set(notices.values_list('status',flat=True)),{'SENT'})
        self.assertEqual(len(mail.outbox),2)
        self.assertTrue(all(message.to==[self.admin.email] for message in mail.outbox))
        html=mail.outbox[0].alternatives[0].content
        self.assertIn('Этап согласования',html)
        self.assertIn('Сотрудник',html);self.assertIn('Причина',html);self.assertIn('Маршрут',html)
        self.assertIn(self.person.display_name,html);self.assertIn(self.group.position,html)
        self.assertIn('Заезд',html);self.assertIn(str(self.camp),html)
        self.assertNotIn('Руководитель проекта ИЛИ Начальник участка',html)
        self.assertIn(f'decision=approve&amp;selected={MovementReviewRequest.objects.get().pk}',html)
        self.assertIn(f'decision=reject&amp;selected={MovementReviewRequest.objects.get().pk}',html)
        self.assertIn('http://192.168.70.9:8002/planning/tasks/',html)
        self.assertIn('http://192.168.70.9:8002/accounts/approval/action/',html)
        self.assertNotIn('http://portal.example.org/planning/tasks/',html)

        # Existing tasks created before the fix are repaired lazily on open.
        task=WorkTask.objects.get(company=self.company,key__startswith='personnel-movement-approval:')
        notices.delete();mail.outbox.clear();self.client.force_login(self.admin)
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.get(reverse('planning:task_detail',args=[task.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(ApprovalNotice.objects.filter(company=self.company,process='MOVEMENT',status='SENT').count(),2)
        self.assertEqual(len(mail.outbox),2)

    def test_approver_cannot_execute_perevakhta_or_dispatcher_facts(self):
        from .departure_workflow import arrive_camp,arrive_hub
        manager=User.objects.create_user(username='facts-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get();row.status='PEREVAKHTA';row.tickets_ready=True;row.save(update_fields=['status','tickets_ready','updated_at'])
        self.complete_trip_details(row)
        with self.assertRaises(PermissionDenied):arrive_hub(manager,row,day)
        row.status='EN_ROUTE';row.hub_departed_on=day;row.save(update_fields=['status','hub_departed_on','updated_at'])
        with self.assertRaises(PermissionDenied):arrive_camp(manager,row,day)

    def test_perevakhta_can_confirm_final_arrival_without_dispatcher(self):
        from .departure_workflow import arrive_camp
        perevakhta=User.objects.create_user(username='final-arrival-perevakhta',company=self.company,
            role=Role.objects.get(code='PEREVAKHTA_SPECIALIST'))
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get();row.status='EN_ROUTE';row.hub_departed_on=day
        row.save(update_fields=['status','hub_departed_on','updated_at'])
        arrive_camp(perevakhta,row,day);row.refresh_from_db()
        self.assertEqual(row.status,'COMPLETED');self.assertEqual(row.final_arrived_on,day)
        self.assertEqual(self.person.stays.get().camp,self.camp)

    def test_site_chief_can_approve_first_gate_without_manager(self):
        from .departure_workflow import decide
        site_chief=User.objects.create_user(username='future-site-chief',company=self.company,role=Role.objects.get(code='SITE_CHIEF'))
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        row=MovementReviewRequest.objects.get();self.client.force_login(site_chief)
        self.assertEqual(self.client.get(reverse('personnel:movement_review_accept',args=[row.pk])).status_code,200)
        decide(site_chief,row,True,'Согласовано начальником участка');row.refresh_from_db()
        self.assertEqual(row.approval_route['SITE_CHIEF']['status'],'APPROVED')
        self.assertEqual(row.approval_route['MANAGER']['status'],'SKIPPED')
        self.assertEqual(row.approval_route['PEREVAKHTA_SPECIALIST']['status'],'PENDING')

    def test_first_gate_waits_for_second_role_after_one_rejection(self):
        from .departure_workflow import active_route_roles,decide
        manager=User.objects.create_user(username='or-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        site_chief=User.objects.create_user(username='or-site-chief',company=self.company,role=Role.objects.get(code='SITE_CHIEF'))
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        row=MovementReviewRequest.objects.get()
        decide(manager,row,False,'Не согласовано руководителем');row.refresh_from_db()
        self.assertEqual(row.status,'PENDING_APPROVAL');self.assertEqual(active_route_roles(row),{'SITE_CHIEF'})
        self.assertEqual(row.approval_route['MANAGER']['status'],'CANCELLED')
        self.assertFalse(row.cancellation_reason);self.assertIsNone(row.cancelled_at)
        decide(site_chief,row,True,'Согласовано начальником');row.refresh_from_db()
        self.assertEqual(row.status,'PENDING_APPROVAL')
        self.assertEqual(row.approval_route['PEREVAKHTA_SPECIALIST']['status'],'PENDING')

    def test_configured_movement_approver_replaces_other_user_with_same_role(self):
        from apps.accounts.approval_assignments import replace
        from .departure_workflow import decide
        first=User.objects.create_user(username='configured-manager-old',company=self.company,role=Role.objects.get(code='MANAGER'))
        selected=User.objects.create_user(username='configured-manager-new',company=self.company,role=Role.objects.get(code='MANAGER'))
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=selected,actor=self.admin,
            construction_object=self.obj,camp=self.camp)
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        row=MovementReviewRequest.objects.get();row.construction_object=self.obj;row.save(update_fields=['construction_object','updated_at'])
        with self.assertRaises(PermissionDenied):decide(first,row,True,'Старый ответственный')
        decide(selected,row,True,'Назначенный ответственный');row.refresh_from_db()
        self.assertEqual(row.approval_route['MANAGER']['status'],'APPROVED')

    def test_first_gate_cancels_only_after_both_roles_reject(self):
        from .departure_workflow import decide
        manager=User.objects.create_user(username='reject-or-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        site_chief=User.objects.create_user(username='reject-or-site-chief',company=self.company,role=Role.objects.get(code='SITE_CHIEF'))
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        row=MovementReviewRequest.objects.get()
        decide(manager,row,False,'Отказ руководителя');row.refresh_from_db()
        decide(site_chief,row,False,'Отказ начальника');row.refresh_from_db()
        self.assertEqual(row.status,'CANCELLED');self.assertIn('Начальник участка',row.cancellation_reason)

    def test_or_gate_migration_advances_legacy_manager_approval(self):
        from importlib import import_module
        from django.apps import apps
        from apps.planning.models import WorkTask
        housing.request_moves(self.personnel,'ARRIVAL',self.camp,timezone.localdate(),[self.person])
        row=MovementReviewRequest.objects.get();task=WorkTask.objects.get(key=f'personnel-movement-approval:{row.pk}')
        legacy={
            'MANAGER':{'status':'APPROVED','comment':'','actor':'','at':''},
            'SITE_CHIEF':{'status':'PENDING','comment':'','actor':'','at':''},
            'PEREVAKHTA_SPECIALIST':{'status':'WAITING','comment':'','actor':'','at':''},
        }
        row.approval_route=legacy;row.save(update_fields=['approval_route','updated_at'])
        task.target_role='MANAGER';task.save(update_fields=['target_role','updated_at'])
        migration=import_module('apps.personnel.migrations.0054_movement_first_approval_or')
        migration.activate_or_gate(apps,None)
        row.refresh_from_db();task.refresh_from_db()
        self.assertEqual(row.approval_route['SITE_CHIEF']['status'],'SKIPPED')
        self.assertEqual(row.approval_route['PEREVAKHTA_SPECIALIST']['status'],'PENDING')
        self.assertEqual(task.target_role,'PEREVAKHTA_SPECIALIST')

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
        self.assertEqual(request_row.requested_movement_date,timezone.localdate())

        self.client.force_login(self.reviewer)
        dispatcher_page=self.client.get(reverse('personnel:housing'))
        self.assertEqual(dispatcher_page.status_code,200)
        self.assertContains(dispatcher_page,'Состав, привязки и графики')
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
        self.assertContains(detail,'Местонахождение')
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
        self.assertEqual(request_row.requested_movement_date,timezone.localdate())
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
        self.assertContains(page,'Диспетчерская');self.assertContains(page,'Перевахта');self.assertContains(page,'Банк сотрудников')
        sidebar=page.content.decode().split('<aside class="sidebar"',1)[1].split('</aside>',1)[0]
        self.assertEqual(sidebar.count(f'href="{reverse("resources:employee_list")}"'),2)
        self.assertEqual(sidebar.count(f'href="{reverse("resources:brigade_list")}"'),2)
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
        self.assertContains(page,f'name="comment_{rows[0].pk}"');self.assertContains(page,f'name="comment_{rows[1].pk}"')
        self.client.post(url,{'action':'movement_approve_selected','selected':[rows[0].pk]})
        rows[0].refresh_from_db();rows[1].refresh_from_db();task.refresh_from_db()
        self.assertEqual(rows[0].status,'PENDING_APPROVAL');self.assertEqual(rows[1].status,'PENDING_APPROVAL');self.assertEqual(task.status,'OPEN')
        missing=self.client.post(url,{'action':'movement_reject_all','comment_'+str(rows[0].pk):'Причина первого'})
        self.assertRedirects(missing,url,fetch_redirect_response=False)
        rows[0].refresh_from_db();rows[1].refresh_from_db()
        self.assertEqual(rows[0].status,'PENDING_APPROVAL');self.assertEqual(rows[1].status,'PENDING_APPROVAL')
        self.client.post(url,{'action':'movement_reject_all','comment_'+str(rows[0].pk):'Причина первого',
            'comment_'+str(rows[1].pk):'Причина второго'})
        rows[0].refresh_from_db();rows[1].refresh_from_db();task.refresh_from_db()
        self.assertEqual(rows[0].status,'CANCELLED');self.assertEqual(rows[1].status,'PENDING_APPROVAL')
        self.assertEqual(rows[0].approval_comment,'Причина первого')
        self.client.post(url,{'action':'movement_reject_all','comment_'+str(rows[1].pk):'Причина второго — окончательно'})
        rows[0].refresh_from_db();rows[1].refresh_from_db();task.refresh_from_db()
        self.assertEqual(rows[0].status,'CANCELLED');self.assertEqual(rows[1].status,'CANCELLED')
        self.assertEqual(rows[1].approval_comment,'Причина второго — окончательно');self.assertIn('Отменено согласующим',rows[1].cancellation_reason)
        self.assertEqual(task.status,'SUPERSEDED')

    def test_email_action_can_decide_one_batch_employee_and_then_all_remaining(self):
        from django.test import Client
        from django.template.loader import render_to_string
        from apps.accounts.approval_mail import context,token
        manager=User.objects.create_user(username='batch-mail-manager',company=self.company,
            role=Role.objects.get(code='MANAGER'))
        perevakhta=User.objects.create_user(username='batch-mail-perevakhta',company=self.company,
            role=Role.objects.get(code='PEREVAKHTA_SPECIALIST'))
        replace(company=self.company,process='MOVEMENT',stage_code='MANAGER',user=manager,actor=self.admin,
            construction_object=self.obj,camp=self.camp)
        replace(company=self.company,process='MOVEMENT',stage_code='PEREVAKHTA_SPECIALIST',user=perevakhta,
            actor=self.admin,construction_object=self.obj,camp=self.camp)
        second_employee=Employee.objects.create(company=self.company,last_name='Почтовый',first_name='Пётр',
            employee_number='review-mail-2',position=self.position)
        second=Person.objects.create(company=self.company,employee=second_employee,
            full_name=second_employee.display_name,employee_number=second_employee.employee_number)
        day=timezone.localdate();housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person,second])
        batch=uuid4();housing.request_moves(self.personnel,'DEPARTURE',self.camp,day,[self.person,second],
            departure_destination='REST',approval_batch=batch,base_city='Уфа')
        rows=list(MovementReviewRequest.objects.filter(approval_batch=batch).order_by('pk'))
        task=WorkTask.objects.get(key=f'personnel-movement-batch:{batch}')
        notice=ApprovalNotice.objects.get(task_id=task.pk,stage_code='MANAGER',intended_user=manager)
        url=reverse('accounts:approval_email_action',args=[token(notice,self.admin)])
        anonymous=Client(enforce_csrf_checks=True)
        response=anonymous.post(url,{'decision':'approve','selected':[rows[0].pk]})
        self.assertContains(response,'Решение сохранено.')
        rows[0].refresh_from_db();rows[1].refresh_from_db()
        self.assertEqual(rows[0].approval_route['MANAGER']['status'],'APPROVED')
        self.assertEqual(rows[1].approval_route['MANAGER']['status'],'PENDING')
        response=anonymous.get(url,{'decision':'reject','selected':rows[1].pk})
        self.assertContains(response,'Решение только по сотруднику')
        self.assertContains(response,second.display_name)
        self.assertContains(response,f'name="comment_{rows[1].pk}"')
        self.assertNotContains(response,'name="comment"')
        response=anonymous.post(url,{'decision':'approve'})
        self.assertContains(response,'Решение сохранено.')
        rows[1].refresh_from_db()
        self.assertEqual(rows[1].approval_route['MANAGER']['status'],'APPROVED')
        review_notice=ApprovalNotice.objects.get(task_id=task.pk,stage_code='PEREVAKHTA_SPECIALIST',
            intended_user=perevakhta)
        review_context=context(review_notice,task,self.admin,{})
        html=render_to_string('accounts/email/approval.html',review_context)
        text=render_to_string('accounts/email/approval.txt',review_context)
        self.assertIn('Открыть задание и проверить',html);self.assertIn('Согласование из письма на этом этапе отключено',html)
        self.assertNotIn('name="decision"',html);self.assertNotIn('decision=approve',html)
        self.assertIn('ТРЕБУЕТСЯ ПРОВЕРКА В ПОРТАЛЕ',text);self.assertNotIn('Согласовать без входа',text)
        blocked_url=reverse('accounts:approval_email_action',args=[token(review_notice,self.admin)])
        blocked=anonymous.post(blocked_url,{'decision':'approve'})
        self.assertEqual(blocked.status_code,200)
        self.assertContains(blocked,'Согласование по ссылке из письма недоступно')
        self.assertNotContains(blocked,'name="decision"')
        for row in rows:
            row.refresh_from_db();self.assertEqual(row.approval_route['PEREVAKHTA_SPECIALIST']['status'],'PENDING')

    def test_my_requests_filter_by_project_camp_employee_and_position(self):
        day=timezone.localdate()
        first=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='PENDING_APPROVAL',construction_object=self.obj,
            planning_position_name=self.position.name,position_name=self.position.name)
        other_project=Project.objects.create(company=self.company,name='Другой проект заявок')
        other_object=ConstructionObject.objects.create(company=self.company,project=other_project,name='Другой объект заявок')
        other_camp=Camp.objects.create(company=self.company,name='Другое местонахождение заявок')
        CampObjectLink.objects.create(company=self.company,camp=other_camp,construction_object=other_object)
        other_position=Position.objects.create(company=self.company,name='Сварщик заявок')
        other_employee=Employee.objects.create(company=self.company,last_name='Петров',first_name='Пётр',employee_number='review-filter-2',position=other_position)
        other_person=Person.objects.create(company=self.company,employee=other_employee,full_name=other_employee.display_name,employee_number=other_employee.employee_number)
        second=MovementReviewRequest.objects.create(company=self.company,person=other_person,kind='ARRIVAL',movement_date=day,
            to_camp=other_camp,requested_by=self.personnel,status='PENDING_APPROVAL',construction_object=other_object,
            planning_position_name=other_position.name,position_name=other_position.name)
        self.client.force_login(self.personnel)
        response=self.client.get(reverse('personnel:my_movement_requests'),{
            'project':self.obj.pk,'camp':[self.camp.pk],'q':'Иванов','position':self.position.pk})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Фильтры моих заявок')
        self.assertContains(response,self.person.display_name)
        self.assertNotContains(response,other_person.display_name)
        visible=[row for group in response.context['groups'] for row in group['items']]
        self.assertEqual(visible,[first]);self.assertNotIn(second,visible)
        self.assertIn(f'project={self.obj.pk}',response.context['movement_filter_query'])
        self.assertIn(f'camp={self.camp.pk}',response.context['movement_filter_query'])

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
        target=Camp.objects.create(company=self.company,name='Местонахождение-2');housing.move(self.reviewer,'ARRIVAL',self.camp,day,[self.person,second])
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
        self.assertIn('1. Выезд из местонахождения',content);self.assertIn('2. Выезд из ОП Дудинка',content)
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
        self.assertEqual(task.target_role,'MOVEMENT_PARALLEL')
        decide(manager,row,True,'Согласовано');row.refresh_from_db()
        with self.assertRaisesMessage(ValidationError,'Сначала заполните обязательные первичные данные'):decide(self.admin,row,True)
        self.finish_approval(row)
        self.assertEqual(row.status,'PEREVAKHTA')
        arrive_hub(self.reviewer,row,day)
        row.refresh_from_db();self.assertEqual(row.status,'AT_HUB')
        self.assertEqual(self.person.stays.get().departed_on,day)
        self.client.force_login(self.reviewer)
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(detail,'Плановые и фактические даты маршрута')
        self.assertContains(detail,'1. Выезд из местонахождения')
        self.assertContains(detail,'2. ОП Дудинка')
        self.assertContains(detail,day.strftime('%d.%m.%Y'))
        self.assertContains(detail,'name="ticket_cost"');self.assertContains(detail,'name="ticket_document"')
        self.assertContains(detail,'name="clearance_ready"');self.assertContains(detail,'Прикрепить обходной лист (необязательно)')
        response=self.client.post(reverse('personnel:movement_review_accept',args=[row.pk]),{
            'workflow_action':'hub_documents','tickets_ready':'1','clearance_ready':'1',
            'ticket_cost':'12345.67','ticket_document':SimpleUploadedFile('departure-ticket.pdf',b'ticket'),
        })
        self.assertRedirects(response,reverse('personnel:movement_review_accept',args=[row.pk]),fetch_redirect_response=False)
        row.refresh_from_db();self.assertTrue(row.tickets_ready);self.assertTrue(row.clearance_ready);self.assertFalse(row.clearance_document)
        self.assertEqual(row.ticket_cost,Decimal('12345.67'));self.assertTrue(row.ticket_document)
        download=self.client.get(reverse('personnel:movement_review_ticket',args=[row.pk]))
        self.assertEqual(download.status_code,200);self.assertEqual(b''.join(download.streaming_content),b'ticket')
        self.assertIn('attachment;',download['Content-Disposition'])
        leave_hub(self.reviewer,row,day)
        row.refresh_from_db();self.assertEqual(row.status,'COMPLETED');self.assertEqual(row.hub_departed_on,day)
        self.assertGreaterEqual(row.workflow_events.count(),14)

    def test_all_route_fact_dates_allow_any_day(self):
        from .departure_workflow import arrive_camp,arrive_hub,leave_hub,update_hub
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get(kind='ARRIVAL');self.finish_approval(row)
        hub_arrival=day+timedelta(days=2);hub_departure=day+timedelta(days=3);camp_arrival=day+timedelta(days=4)
        row.planned_hub_departure_date=hub_departure;row.save(update_fields=['planned_hub_departure_date','updated_at'])
        update_hub(self.reviewer,row,tickets_ready=True,planned_hub_arrival_date=hub_arrival)
        with self.assertRaisesMessage(ValidationError,'после прибытия сотрудника'):update_hub(self.reviewer,row,clearance_document=SimpleUploadedFile('too-early.pdf',b'pdf'))
        arrive_hub(self.reviewer,row,hub_arrival);row.refresh_from_db();self.assertEqual(row.hub_arrived_on,hub_arrival)
        update_hub(self.reviewer,row,clearance_document=SimpleUploadedFile('clearance.pdf',b'pdf'))
        unrestricted_departure=day+timedelta(days=1)
        leave_hub(self.reviewer,row,unrestricted_departure);row.refresh_from_db();self.assertEqual(row.hub_departed_on,unrestricted_departure)
        arrive_camp(self.reviewer,row,camp_arrival);row.refresh_from_db()
        self.assertEqual(row.final_arrived_on,camp_arrival);self.assertEqual(row.status,'COMPLETED')
        self.assertEqual(self.person.stays.get().arrived_on,camp_arrival)

    def test_ticket_purchase_can_change_planned_dudinka_arrival(self):
        from .departure_workflow import update_hub
        day=timezone.localdate();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person])
        row=MovementReviewRequest.objects.get(kind='ARRIVAL');self.assertEqual(row.requested_movement_date,day);self.finish_approval(row)
        changed=day+timedelta(days=2)
        row.planned_hub_departure_date=day+timedelta(days=3);row.save(update_fields=['planned_hub_departure_date','updated_at'])
        self.client.force_login(self.reviewer);page=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(page,'name="planned_hub_arrival_date"');self.assertContains(page,'Дата прибытия в ОП Дудинка по билету')
        self.assertContains(page,'name="ticket_cost"');self.assertContains(page,'name="ticket_document"')
        self.assertNotContains(page,f'value="{day.isoformat()}"')
        self.assertNotContains(page,f'max="{(day+timedelta(days=3)).isoformat()}"')
        response=self.client.post(reverse('personnel:movement_review_accept',args=[row.pk]),{
            'workflow_action':'hub_documents','tickets_ready':'1','planned_hub_arrival_date':changed.isoformat(),
            'ticket_cost':'4567.80','ticket_document':SimpleUploadedFile('arrival-ticket.pdf',b'arrival-ticket'),
        })
        self.assertRedirects(response,reverse('personnel:movement_review_accept',args=[row.pk]),fetch_redirect_response=False)
        row.refresh_from_db();self.assertEqual(row.movement_date,changed);self.assertEqual(row.requested_movement_date,day);self.assertTrue(row.tickets_ready)
        self.assertEqual(row.ticket_cost,Decimal('4567.80'));self.assertTrue(row.ticket_document)
        detail=self.client.get(reverse('personnel:movement_review_accept',args=[row.pk]))
        self.assertContains(detail,'Плановая дата заезда, указанная диспетчером')
        self.assertContains(detail,'План диспетчера')
        queue=self.client.get(reverse('personnel:movement_reviews'),{'kind':'arrival'})
        self.assertContains(queue,'Заезд в местонахождение / ОП Дудинка');self.assertContains(queue,'ОП Дудинка:')
        row.tickets_ready=False;row.save(update_fields=['tickets_ready','updated_at'])
        later=day+timedelta(days=4)
        update_hub(self.reviewer,row,tickets_ready=True,planned_hub_arrival_date=later)
        row.refresh_from_db();self.assertEqual(row.movement_date,later);self.assertTrue(row.tickets_ready)

    def test_parallel_rejection_cancels_person_and_preserves_approval_history(self):
        from .departure_workflow import active_route_roles,decide,resubmit
        day=timezone.localdate();batch=uuid4();housing.request_moves(self.personnel,'ARRIVAL',self.camp,day,[self.person],approval_batch=batch)
        row=MovementReviewRequest.objects.get();self.complete_trip_details(row)
        for code in ['MANAGER','PEREVAKHTA_SPECIALIST']:
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
        update_hub(self.reviewer,row,clearance_ready=True);row.refresh_from_db()
        self.assertTrue(row.clearance_ready);self.assertFalse(row.clearance_document)
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

    def test_review_filters_apply_to_arrival_and_departure_lists(self):
        day=timezone.localdate()
        other_project=Project.objects.create(company=self.company,name='Другой проект')
        other_object=ConstructionObject.objects.create(company=self.company,project=other_project,name='Другой объект')
        other_camp=Camp.objects.create(company=self.company,name='Местонахождение-2')
        arrival=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,construction_object=self.obj,requested_by=self.personnel,request_number='FIND-42')
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=other_camp,construction_object=other_object,requested_by=self.reviewer,source='EXCEL',status='PEREVAKHTA')
        departure=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',movement_date=day,
            from_camp=self.camp,construction_object=self.obj,requested_by=self.reviewer,source='EXCEL',status='PEREVAKHTA')
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_reviews')
        page=self.client.get(url,{'q':'FIND-42','project':self.project.pk,'camp':self.camp.pk,
            'status':'OPEN','source':'DISPATCHER'})
        self.assertEqual([row.pk for row in page.context['dispatcher_review_requests']],[arrival.pk])
        self.assertEqual(page.context['excel_review_requests'],[])
        self.assertContains(page,'Фильтры заявок перевахты');self.assertContains(page,'Все проекты');self.assertContains(page,'Все местонахождения')
        self.assertContains(page,f'q=FIND-42&amp;project={self.project.pk}')
        page=self.client.get(url,{'kind':'departure','project':self.project.pk,'camp':self.camp.pk,
            'status':'PEREVAKHTA','source':'EXCEL'})
        self.assertEqual([row.pk for row in page.context['excel_review_requests']],[departure.pk])
        self.assertEqual(page.context['dispatcher_review_requests'],[])

    def test_review_lifecycle_cards_split_real_workflow_steps(self):
        from .departure_workflow import PARALLEL,initial_route
        day=timezone.localdate()
        first_route=initial_route()
        first=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='PENDING_APPROVAL',approval_route=first_route)
        specialist_route=initial_route();specialist_route['MANAGER']['status']='APPROVED';specialist_route['SITE_CHIEF']['status']='SKIPPED';specialist_route['PEREVAKHTA_SPECIALIST']['status']='PENDING'
        specialist=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='PENDING_APPROVAL',approval_route=specialist_route)
        parallel_route=initial_route();parallel_route['MANAGER']['status']='APPROVED';parallel_route['SITE_CHIEF']['status']='SKIPPED';parallel_route['PEREVAKHTA_SPECIALIST']['status']='APPROVED'
        for code in PARALLEL:parallel_route[code]['status']='PENDING'
        parallel=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='PENDING_APPROVAL',approval_route=parallel_route)
        tickets=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='PEREVAKHTA',tickets_ready=False)
        hub_processing=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='AT_HUB',clearance_ready=False)
        hub_ready=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='AT_HUB',clearance_ready=True)
        completed=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='COMPLETED')
        accepted=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,requested_by=self.personnel,status='ACCEPTED')
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_reviews')
        page=self.client.get(url)
        cards={card['code']:card for card in page.context['review_status_cards']}
        self.assertEqual(cards['APPROVAL_FIRST']['count'],1)
        self.assertEqual(cards['APPROVAL_PEREVAKHTA']['count'],1)
        self.assertEqual(cards['APPROVAL_PARALLEL']['count'],1)
        self.assertEqual(cards['TICKETS']['count'],1)
        self.assertEqual(cards['HUB_PROCESSING']['count'],1);self.assertEqual(cards['HUB_READY']['count'],1)
        self.assertEqual(cards['COMPLETED']['count'],2);self.assertNotIn('ACCEPTED',cards)
        self.assertContains(page,'Этап жизни заявки');self.assertContains(page,'Параллельное согласование');self.assertContains(page,'Покупка билетов')
        self.assertContains(page,'Оформление в ОП Дудинка');self.assertNotContains(page,'Оформление в ЗУП</span>');self.assertNotContains(page,'Обходной лист</span>')
        self.assertContains(page,'Документы готовы / к выезду из ОП Дудинка')
        self.assertNotContains(page,'>Принято</span>')
        filtered=self.client.get(url,{'status':'APPROVAL_PEREVAKHTA'})
        self.assertEqual([row.pk for row in filtered.context['dispatcher_review_requests']],[specialist.pk])
        self.assertNotIn(first,filtered.context['dispatcher_review_requests']);self.assertNotIn(parallel,filtered.context['dispatcher_review_requests']);self.assertNotIn(tickets,filtered.context['dispatcher_review_requests'])
        self.assertNotIn(hub_processing,filtered.context['dispatcher_review_requests']);self.assertNotIn(hub_ready,filtered.context['dispatcher_review_requests'])
        completed_page=self.client.get(url,{'status':'COMPLETED'})
        self.assertEqual({row.pk for row in completed_page.context['dispatcher_review_requests']},{completed.pk,accepted.pk})

    def test_position_summary_combines_rest_arrivals_departures_and_exports_excel(self):
        day=timezone.localdate();position=self.employee.display_position
        self.slot.status='МО';self.slot.housing_camp=self.camp
        self.slot.save(update_fields=['status','housing_camp','updated_at'])
        for employment_kind in ('NEW','STAFF','REHIRE','TRANSFER'):
            MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
                to_camp=self.camp,construction_object=self.obj,requested_by=self.personnel,
                planning_position_name=position,employment_kind=employment_kind)
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',movement_date=day,
            from_camp=self.camp,construction_object=self.obj,requested_by=self.personnel,planning_position_name=position)
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',movement_date=day,
            from_camp=self.camp,construction_object=self.obj,requested_by=self.personnel,planning_position_name=position,status='AT_HUB')
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',movement_date=day,
            to_camp=self.camp,construction_object=self.obj,requested_by=self.personnel,planning_position_name=position,
            employment_kind='NEW',status='CANCELLED')
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',
            movement_date=day+timedelta(days=1),to_camp=self.camp,construction_object=self.obj,
            requested_by=self.personnel,planning_position_name=position,employment_kind='BUSINESS_TRIP')
        MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='DEPARTURE',
            movement_date=day+timedelta(days=1),from_camp=self.camp,construction_object=self.obj,
            requested_by=self.personnel,planning_position_name=position)
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_position_summary')
        params={'date':day.isoformat(),'camp':self.camp.pk}
        response=self.client.get(url,params)
        row=next(item for item in response.context['rows'] if item['position']==position)
        self.assertEqual((row['on_rest'],row['arrival_new'],row['arrival_staff'],row['arrival_other'],row['departure']),
            (1,1,1,2,1))
        self.assertContains(response,'Сводные отчёты перевахты');self.assertContains(response,'Планируются к выезду')
        statuses={row['code']:row for row in response.context['status_rows']}
        self.assertEqual(statuses['REHIRE']['arrival_today'],1)
        self.assertEqual(statuses['BUSINESS_TRIP']['arrival_tomorrow'],1)
        self.assertEqual(statuses['STAFF']['departure_today'],1)
        self.assertEqual(statuses['STAFF']['departure_tomorrow'],1)
        export=self.client.get(url,{**params,'export':'xlsx'})
        self.assertEqual(export['Content-Type'],'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(export.content),read_only=True);sheet=book.active
        self.assertEqual([cell.value for cell in sheet[1]],['№ п.п.','Должность','Кол-во сотрудников на МО',
            'Поставлены на заезд (новые)','Поставлены на заезд (штат)',
            'Поставлены на заезд (перевод / командировка)','Планируются к выезду'])
        book.close()
        status_export=self.client.get(url,{**params,'export':'status'})
        status_book=load_workbook(BytesIO(status_export.content),read_only=True);status_sheet=status_book.active
        self.assertEqual([cell.value for cell in status_sheet[1]],['Статус',f'Заявлено на заезд {day:%d.%m.%Y}',
            f'Заявлено на заезд {day+timedelta(days=1):%d.%m.%Y}',f'Заявлено на выезд {day:%d.%m.%Y}',
            f'Заявлено на выезд {day+timedelta(days=1):%d.%m.%Y}'])
        status_book.close()
        from .movement_excel_import import employment
        self.assertEqual(employment('Повторный прием'),'REHIRE')

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
        self.assertContains(sorted_page,'Заезд в местонахождение / ОП Дудинка ↑');self.assertContains(sorted_page,'Создана')

    def test_visible_excel_sheets_create_review_requests_without_duplicates(self):
        from openpyxl import Workbook
        book=Workbook();arrival=book.active;arrival.title='Заезд Ноябрь 26'
        arrival.append(['№','штат/новый/перевод','Фамилия Имя Отчество','Должность','Участок','Базовый город','Номер телефона','№ заявки','Дата заезда','Время прилета','Статус'])
        arrival.append([1,'Штат',self.employee.display_name,'Электромонтёр СМГ',self.site.name,'Уфа','8 900 000 00 00',302,timezone.localdate(),'08:45','Ожидается'])
        arrival.append([2,'Штат',self.employee.display_name,'Электромонтёр СМГ',self.site.name,'Уфа','8 900 000 00 00',304,timezone.localdate()+timedelta(days=1),'08:45','Прибыл'])
        arrival.append([3,'Новый','Неизвестный Человек','Рабочий',self.site.name,'Омск','',303,timezone.localdate(),'09:00','Ожидается'])
        archive=book.create_sheet('Выезд Январь 24');archive.sheet_state='hidden'
        archive.append(['Фамилия Имя Отчество','Дата выезда']);archive.append([self.employee.display_name,timezone.localdate()])
        stream=BytesIO();book.save(stream);upload=lambda:SimpleUploadedFile('perevakhta.xlsx',stream.getvalue(),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        self.client.force_login(self.reviewer)
        response=self.client.post(reverse('personnel:movement_reviews'),{'review_action':'import_excel','movement_excel':upload()})
        self.assertEqual(response.status_code,200);self.assertContains(response,'создано 2');self.assertContains(response,'сразу в историю 1');self.assertContains(response,'Неизвестный Человек')
        row=MovementReviewRequest.objects.get(request_number='302');self.assertEqual(row.person,self.person);self.assertEqual(row.source,'EXCEL');self.assertEqual(row.site_object,self.site);self.assertEqual(row.to_camp,self.camp)
        archived=MovementReviewRequest.objects.get(request_number='304');self.assertEqual(archived.status,'COMPLETED');self.assertEqual(archived.travel_status,'Прибыл')
        self.assertIn(archived,response.context['excel_history_requests']);self.assertNotIn(archived,response.context['excel_review_requests'])
        response=self.client.post(reverse('personnel:movement_reviews'),{'review_action':'import_excel','movement_excel':upload()})
        self.assertEqual(MovementReviewRequest.objects.count(),2);self.assertContains(response,'повторов 2')

    def test_excel_site_mapping_updates_pending_rows_and_drives_future_imports(self):
        raw_site='Талнах  Строительный участок №3'
        pending=MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',
            movement_date=timezone.localdate(),requested_by=self.reviewer,source='EXCEL',site=raw_site,request_number='901')
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_excel_sites')
        page=self.client.get(url);self.assertEqual(page.status_code,200);self.assertContains(page,raw_site)
        response=self.client.post(url,{'source_site':'Талнах Строительный участок №3',
            'construction_object':self.site.pk,'camp':self.camp.pk},follow=True)
        self.assertContains(response,'Обновлено необработанных Excel-заявок: 1')
        mapping=MovementExcelSiteMapping.objects.get();self.assertEqual(mapping.source_site_normalized,'талнах строительный участок №3')
        pending.refresh_from_db();self.assertEqual(pending.site_object,self.site);self.assertEqual(pending.construction_object,self.obj)
        self.assertEqual(pending.to_camp,self.camp)
        from openpyxl import Workbook
        book=Workbook();sheet=book.active;sheet.title='Заезд Ноябрь 26'
        sheet.append(['Фамилия Имя Отчество','Должность','Участок','Базовый город','№ заявки','Дата заезда','Статус'])
        sheet.append([self.employee.display_name,'Электромонтёр СМГ','ТАЛНАХ строительный участок №3','Уфа',902,
            timezone.localdate()+timedelta(days=1),'Ожидается'])
        stream=BytesIO();book.save(stream)
        from .movement_excel_import import apply
        result=apply(self.reviewer,stream.getvalue());self.assertEqual(result['site_mapped'],1)
        imported=MovementReviewRequest.objects.get(request_number='902')
        self.assertEqual(imported.site_object,self.site);self.assertEqual(imported.construction_object,self.obj)
        self.assertEqual(imported.to_camp,self.camp)

    def test_excel_site_mapping_rejects_unrelated_camp_and_duplicate_normalized_name(self):
        other=Camp.objects.create(company=self.company,name='Другое местонахождение')
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_excel_sites')
        response=self.client.post(url,{'source_site':'Участок №1','construction_object':self.site.pk,'camp':other.pk})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Местонахождение должно быть связано')
        MovementExcelSiteMapping.objects.create(company=self.company,source_site='Участок №1',construction_object=self.site,camp=self.camp)
        response=self.client.post(url,{'source_site':'  УЧАСТОК   №1 ','construction_object':self.site.pk,'camp':self.camp.pk})
        self.assertContains(response,'Для этого участка соответствие уже настроено')

    def test_excel_site_mappings_can_be_assigned_in_bulk(self):
        rows=[]
        for number,site in enumerate(('Участок А','Участок Б','Участок В'),start=1):
            rows.append(MovementReviewRequest.objects.create(company=self.company,person=self.person,kind='ARRIVAL',
                movement_date=timezone.localdate(),requested_by=self.reviewer,source='EXCEL',site=site,
                request_number=f'bulk-{number}'))
        self.client.force_login(self.reviewer);url=reverse('personnel:movement_excel_sites')
        page=self.client.get(url)
        self.assertContains(page,'Массовая привязка');self.assertContains(page,'Выбрать ненастроенные')
        response=self.client.post(url,{'action':'bulk_assign','selected_sites':['Участок А','Участок Б'],
            'construction_object':self.site.pk,'camp':self.camp.pk},follow=True)
        self.assertContains(response,'Привязано соответствий: 2 (новых: 2)')
        self.assertContains(response,'Обновлено необработанных Excel-заявок: 2')
        self.assertEqual(MovementExcelSiteMapping.objects.count(),2)
        for row in rows[:2]:
            row.refresh_from_db();self.assertEqual(row.site_object,self.site);self.assertEqual(row.construction_object,self.obj)
            self.assertEqual(row.to_camp,self.camp)
        rows[2].refresh_from_db();self.assertIsNone(rows[2].site_object);self.assertIsNone(rows[2].to_camp)
