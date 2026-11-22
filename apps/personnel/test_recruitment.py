from datetime import date,datetime,timedelta
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Company, Role, User
from apps.projects.models import Project, ConstructionObject
from apps.resources.models import Employee,Position
from .models import (Camp, CampObjectLink, CampStay, Person, RecruitmentRequest,
                     RecruitmentCandidate, ArrivalPreparation, CandidateDocument, HousingEvent,BaseCity,
                     MovementReviewRequest,TalentPoolCandidate,TalentPoolStageEvent)
from . import recruitment as service


class RecruitmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Подбор test')
        cls.other=Company.objects.create(name='Другая компания')
        cls.project=Project.objects.create(company=cls.company,name='Проект')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='КТП')
        cls.position=Position.objects.create(company=cls.company,name='Монтажник',code='montazhnik')
        cls.base_city=BaseCity.objects.create(company=cls.company,name='Уфа')
        cls.camp=Camp.objects.create(company=cls.company,name='Местонахождение')
        CampObjectLink.objects.create(company=cls.company,camp=cls.camp,construction_object=cls.obj)
        cls.unlinked=Camp.objects.create(company=cls.company,name='Другое местонахождение')
        cls.foreign_camp=Camp.objects.create(company=cls.other,name='Чужое местонахождение')
        cls.day=timezone.localdate()-timedelta(days=1)
        cls.dispatch=User.objects.create_user(username='dispatch-recruit',company=cls.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        cls.hr_head=User.objects.create_user(username='hr-head-recruit',first_name='Ирина',last_name='Руководитель',company=cls.company,role=Role.objects.get(code='HR_HEAD'))
        cls.recruiter=User.objects.create_user(username='recruiter',company=cls.company,role=Role.objects.get(code='RECRUITER'))
        cls.recruiter2=User.objects.create_user(username='recruiter2',company=cls.company,role=Role.objects.get(code='RECRUITER'))
        cls.recruitment_head=User.objects.create_user(username='recruitment-head',company=cls.company,role=Role.objects.get(code='RECRUITMENT_HEAD'))
        cls.perevakhta=User.objects.create_user(username='recruitment-perevakhta',company=cls.company,role=Role.objects.get(code='PEREVAKHTA_SPECIALIST'))
        cls.admin=User.objects.create_user(username='recruitment-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
        cls.foreign=User.objects.create_user(username='recruit-foreign',company=cls.other,role=Role.objects.get(code='RECRUITER'))
        cls.foreign_hr_head=User.objects.create_user(username='hr-head-foreign',company=cls.other,role=Role.objects.get(code='HR_HEAD'))
        cls.employee=Employee.objects.create(company=cls.company,last_name='Оформленный',first_name='Сотрудник',employee_number='R-001')
        cls.foreign_employee=Employee.objects.create(company=cls.other,last_name='Чужой',first_name='Сотрудник')
    def values(self,**extra):
        return {'construction_object':self.obj,'position_ref':self.position,'position':'Монтажник','quantity':2,'required_on':self.day,'requirements':'Удостоверение',**extra}
    def setUp(self):
        self._candidate_sequence=0
    def row(self):
        return service.save_request(self.dispatch,self.values())
    def candidate(self,row=None,**extra):
        row=row or self.row()
        service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        self._candidate_sequence+=1
        return service.save_candidate(self.recruiter,row.pk,{
            'full_name':f'Кандидат Новый {self._candidate_sequence}',
            'phone':f'123{self._candidate_sequence}',
            'status':'READY','ready_on':self.day,'notes':'Согласован',**extra})
    def preparation(self):
        c=self.candidate();return service.handoff(self.recruiter,c.pk,c.revision)
    def arrival_values(self,**extra):
        return {'expected_on':self.day,'camp':self.camp,'employee':self.employee,'notes':'Заезд','actual_on':self.day,**extra}
    def assertValidation(self,fn,*args,**kwargs):
        with self.assertRaises(ValidationError):fn(*args,**kwargs)

    def test_complete_flow_counts_and_no_automatic_employee_or_fact(self):
        row=self.row();self.assertEqual(service.progress(row),{'required':2,'shortlisted':0,'ready':0,'arrived':0,'label':'Новая'})
        before=Employee.objects.count();c=self.candidate(row)
        self.assertIsNotNone(c.pool_candidate_id);self.assertEqual(c.pool_candidate.stage,'READY')
        self.assertEqual(Employee.objects.count(),before);self.assertFalse(Person.objects.exists());self.assertFalse(CampStay.objects.exists())
        prep=service.handoff(self.recruiter,c.pk,c.revision)
        c.pool_candidate.refresh_from_db();self.assertEqual(c.pool_candidate.stage,'TRANSFERRED')
        self.assertEqual(service.progress(RecruitmentRequest.objects.get(pk=row.pk))['ready'],1)
        self.assertFalse(CampStay.objects.exists())
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values())
        self.assertFalse(CampStay.objects.exists());self.assertFalse(Person.objects.exists())
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        c.pool_candidate.refresh_from_db();self.assertEqual(c.pool_candidate.stage,'ARRIVED');self.assertFalse(c.pool_candidate.is_active)
        self.assertTrue(prep.actually_arrived);self.assertEqual(prep.actual_stay.person.employee_id,self.employee.pk)
        self.assertEqual(HousingEvent.objects.get().kind,'ARRIVAL');self.assertEqual(service.progress(RecruitmentRequest.objects.get(pk=row.pk))['arrived'],1)
        self.assertEqual(Employee.objects.count(),before)

    def test_zup_employee_moves_unique_candidate_from_store_to_employed(self):
        from .recruitment_employment import reconcile_company
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Иванов Иван Иванович',phone='8 999 123-45-67',
            stage='SELECTED',position=self.position,construction_object=self.obj,created_by=self.recruiter)
        employee=Employee.objects.create(company=self.company,zup_full_name='Иванов Иван Иванович',phone='+7 999 123-45-67',
            employee_number='Z-100',zup_ref='a'*32,zup_server='mssql',zup_database='zup',zup_synced_at=timezone.now())
        self.assertEqual(reconcile_company(self.company,self.recruiter),{'linked':1,'ambiguous':0})
        candidate.refresh_from_db()
        self.assertEqual(candidate.stage,'EMPLOYED');self.assertEqual(candidate.employed_employee,employee);self.assertFalse(candidate.is_active)
        self.assertTrue(TalentPoolStageEvent.objects.filter(candidate=candidate,from_stage='SELECTED',to_stage='EMPLOYED').exists())
        self.client.force_login(self.recruiter);page=self.client.get(reverse('personnel:recruitment_list'))
        groups={row['label']:row['count'] for row in page.context['funnel_groups']}
        self.assertEqual(groups['Магазин'],0);self.assertEqual(groups['Трудоустроены'],1)
        response=self.client.post(reverse('personnel:recruitment_candidate_stage',args=[candidate.pk]),{'stage':'SCREENING'})
        self.assertEqual(response.status_code,400);candidate.refresh_from_db();self.assertEqual(candidate.stage,'EMPLOYED')

    def test_zup_employment_does_not_guess_between_duplicate_candidate_cards(self):
        from .recruitment_employment import REVIEW_KEY,reconcile_company
        candidates=[]
        for suffix in ('А','Б'):
            candidates.append(TalentPoolCandidate.objects.create(company=self.company,full_name='Петров Пётр Петрович',phone='',stage='SELECTED',
                position=self.position,construction_object=self.obj,created_by=self.recruiter,notes=suffix))
        employee=Employee.objects.create(company=self.company,zup_full_name='Петров Пётр Петрович',employee_number='Z-200',
            zup_ref='b'*32,zup_server='mssql',zup_database='zup',zup_synced_at=timezone.now())
        self.assertEqual(reconcile_company(self.company,self.recruiter),{'linked':0,'ambiguous':1})
        self.assertFalse(TalentPoolCandidate.objects.filter(employed_employee__isnull=False).exists())
        for candidate in candidates:
            candidate.refresh_from_db();self.assertEqual(candidate.stage,'SELECTED')
            self.assertEqual(candidate.source_data[REVIEW_KEY]['employee_ids'],[employee.pk])
        self.client.force_login(self.recruiter)
        page=self.client.get(reverse('personnel:recruitment_list'),{'candidate_stage':'employed'})
        groups={row['label']:row['count'] for row in page.context['funnel_groups']}
        self.assertEqual(groups['Магазин'],2);self.assertEqual(groups['Трудоустроены'],2)
        review=self.client.get(reverse('personnel:recruitment_candidate_inline',args=[candidates[0].pk]))
        self.assertContains(review,'Проверить соответствие и связать');self.assertContains(review,'Z-200')
        self.client.force_login(self.dispatch)
        forbidden=self.client.post(reverse('personnel:recruitment_candidate_employment',args=[candidates[0].pk]),{'employee':employee.pk})
        self.assertEqual(forbidden.status_code,403)
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_candidate_employment',args=[candidates[0].pk]),
            {'employee':employee.pk},follow=True)
        self.assertContains(response,'связан с сотрудником ЗУП')
        candidates[0].refresh_from_db();candidates[1].refresh_from_db()
        self.assertEqual(candidates[0].stage,'EMPLOYED');self.assertEqual(candidates[0].employed_employee,employee)
        self.assertNotIn(REVIEW_KEY,candidates[1].source_data);self.assertEqual(candidates[1].stage,'SELECTED')

    def test_zup_employment_review_offers_same_name_employees_for_manual_choice(self):
        from .recruitment_employment import REVIEW_KEY,reconcile_company
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Сидоров Семён Сергеевич',stage='SELECTED',
            position=self.position,construction_object=self.obj,created_by=self.recruiter)
        employees=[Employee.objects.create(company=self.company,zup_full_name='Сидоров Семён Сергеевич',employee_number=f'Z-30{index}',
            zup_ref=str(index)*32,zup_server='mssql',zup_database='zup',zup_synced_at=timezone.now()) for index in (1,2)]
        self.assertEqual(reconcile_company(self.company,self.recruiter),{'linked':0,'ambiguous':1})
        candidate.refresh_from_db();self.assertEqual(candidate.stage,'SELECTED')
        self.assertEqual(candidate.source_data[REVIEW_KEY]['employee_ids'],[row.pk for row in employees])
        self.client.force_login(self.recruiter)
        self.client.post(reverse('personnel:recruitment_candidate_employment',args=[candidate.pk]),{'employee':employees[1].pk})
        candidate.refresh_from_db();self.assertEqual(candidate.stage,'EMPLOYED');self.assertEqual(candidate.employed_employee,employees[1])
        self.assertTrue(TalentPoolStageEvent.objects.filter(candidate=candidate,to_stage='EMPLOYED',note__contains='ручной проверки').exists())
    def test_candidate_can_be_sent_from_vacancy_into_arrival_workflow(self):
        from apps.planning.models import WorkTask
        prep=self.preparation()
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),workflow=True)
        self.assertIsNotNone(prep.movement_request_id)
        self.assertEqual(prep.movement_request.status,'PENDING_APPROVAL')
        self.assertEqual(prep.movement_request.employment_kind,'NEW')
        task=WorkTask.objects.get(key=f'personnel-movement-approval:{prep.movement_request_id}',target_role='MOVEMENT_PARALLEL')
        manager=User.objects.create_user(username='recruitment-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        self.client.force_login(manager)
        task_page=self.client.get(reverse('planning:task_detail',args=[task.pk]))
        self.assertEqual(task_page.status_code,200)
        self.assertContains(task_page,'Сотрудники в заявке')
        self.assertContains(task_page,prep.movement_request.person.display_name)
        self.assertContains(task_page,'Согласовать выбранных')
        self.client.post(reverse('planning:task_detail',args=[task.pk]),{
            'action':'movement_approve_selected','selected':[prep.movement_request_id]})
        prep.movement_request.refresh_from_db()
        self.assertEqual(prep.movement_request.approval_route['MANAGER']['status'],'APPROVED')
        self.assertFalse(CampStay.objects.exists())
    def test_rejected_arrival_returns_candidate_to_recommendations(self):
        from .departure_workflow import decide
        prep=self.preparation();prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),workflow=True)
        manager=User.objects.create_user(username='reject-recruitment-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        decide(manager,prep.movement_request,False,'Перенести заезд')
        prep.refresh_from_db();self.assertIsNotNone(prep.movement_request_id)
        site_chief=User.objects.create_user(username='reject-recruitment-site-chief',company=self.company,role=Role.objects.get(code='SITE_CHIEF'))
        decide(site_chief,prep.movement_request,False,'Начальник участка тоже не согласовал')
        prep.refresh_from_db();prep.candidate.refresh_from_db()
        self.assertIsNone(prep.movement_request_id);self.assertEqual(prep.candidate.status,'READY')
        self.client.force_login(self.dispatch)
        self.assertContains(self.client.get(reverse('personnel:new_employee_arrivals')),prep.candidate.full_name)
    def test_manual_duplicate_is_blocked_across_active_requests(self):
        first=self.candidate();second_request=self.row();service.assign_request(self.hr_head,second_request.pk,self.recruiter,second_request.revision)
        with self.assertRaisesMessage(ValidationError,'уже участвует'):
            service.save_candidate(self.recruiter,second_request.pk,{'pool_candidate':None,'full_name':first.full_name,
                'phone':first.phone,'status':'READY','ready_on':self.day,'notes':'','documents':[]},revision=0,files=[])
    def test_whole_bulk_draft_can_be_cancelled_and_releases_candidates(self):
        row=service.save_request(self.dispatch,self.values(quantity=1));candidate=self.candidate(row)
        batch,preparations=service.bulk_prepare_arrivals(self.dispatch,[candidate.pk])
        self.assertTrue(preparations[0].movement_request_id)
        service.cancel_bulk_arrival_draft(self.dispatch,batch,'Изменился план')
        preparations[0].refresh_from_db();candidate.refresh_from_db()
        self.assertIsNone(preparations[0].movement_request_id);self.assertEqual(candidate.status,'READY')
    def test_dispatcher_can_create_one_arrival_batch_from_selected_candidates(self):
        from apps.planning.models import WorkTask
        row=self.row();service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        candidates=[]
        for number in (1,2):
            candidates.append(service.save_candidate(self.recruiter,row.pk,{
                'pool_candidate':None,'full_name':f'Новый кандидат {number}','phone':f'8999000000{number}',
                'status':'READY','ready_on':self.day,'notes':'','documents':[]},revision=0,files=[]))
        self.client.force_login(self.dispatch)
        page=self.client.get(reverse('personnel:new_employee_arrivals'))
        self.assertContains(page,'Оформить выбранных одной заявкой')
        response=self.client.post(reverse('personnel:recruitment_arrival_bulk'),{
            'selected':[candidate.pk for candidate in candidates]})
        preparations=list(ArrivalPreparation.objects.filter(candidate__in=candidates).select_related('movement_request'))
        self.assertEqual(len(preparations),2)
        batches={prep.movement_request.approval_batch for prep in preparations}
        self.assertEqual(len(batches),1);self.assertNotIn(None,batches)
        batch=batches.pop()
        self.assertRedirects(response,reverse('personnel:recruitment_arrival_bulk_edit',args=[batch]),fetch_redirect_response=False)
        self.assertEqual(WorkTask.objects.filter(key=f'personnel-movement-batch:{batch}').count(),0)
        draft=self.client.get(reverse('personnel:recruitment_arrival_bulk_edit',args=[batch]))
        self.assertContains(draft,'Общие данные для всех сотрудников');self.assertContains(draft,'Сохранить черновик')
        mine=self.client.get(reverse('personnel:my_movement_requests'))
        self.assertContains(mine,'Черновик');self.assertContains(mine,'Продолжить заполнение')
        payload={'action':'submit','construction_object':self.obj.pk,'camp':self.camp.pk}
        for prep in preparations:
            request_id=prep.movement_request_id
            payload.update({f'row-{request_id}-planning_position_name':'Монтажник',f'row-{request_id}-position_name':'Монтажник',
                f'row-{request_id}-base_city':'Уфа',f'row-{request_id}-phone':prep.candidate.phone,
                f'row-{request_id}-movement_date':self.day.isoformat(),f'row-{request_id}-planned_hub_departure_date':self.day.isoformat(),
                f'row-{request_id}-note':''})
        submitted=self.client.post(reverse('personnel:recruitment_arrival_bulk_edit',args=[batch]),payload)
        self.assertRedirects(submitted,reverse('personnel:my_movement_requests'),fetch_redirect_response=False)
        self.assertEqual(WorkTask.objects.filter(key=f'personnel-movement-batch:{batch}').count(),1)
        for prep in preparations:
            prep.movement_request.refresh_from_db();self.assertEqual(prep.movement_request.status,'PENDING_APPROVAL')
            self.assertEqual(prep.movement_request.to_camp,self.camp);self.assertEqual(prep.movement_request.construction_object,self.obj)
        self.assertFalse(RecruitmentCandidate.objects.filter(pk__in=[candidate.pk for candidate in candidates],arrival__movement_request__isnull=True).exists())

    def test_new_employee_arrivals_filter_by_project_camp_employee_and_position(self):
        first=self.candidate();first.pool_candidate.camp=self.camp;first.pool_candidate.save(update_fields=['camp','updated_at'])
        other_project=Project.objects.create(company=self.company,name='Другой проект')
        other_object=ConstructionObject.objects.create(company=self.company,project=other_project,name='Другой объект')
        other_position=Position.objects.create(company=self.company,name='Сварщик',code='welder')
        other_camp=Camp.objects.create(company=self.company,name='Местонахождение другого проекта')
        CampObjectLink.objects.create(company=self.company,camp=other_camp,construction_object=other_object)
        other_request=service.save_request(self.dispatch,self.values(construction_object=other_object,position_ref=other_position,position=other_position.name))
        second=self.candidate(other_request);second.pool_candidate.camp=other_camp;second.pool_candidate.save(update_fields=['camp','updated_at'])
        self.client.force_login(self.dispatch)
        response=self.client.get(reverse('personnel:new_employee_arrivals'),{
            'project':self.obj.pk,'camp':[self.camp.pk],'q':first.full_name.split()[0],'position':self.position.pk})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Фильтры новых сотрудников')
        self.assertContains(response,first.full_name)
        self.assertNotContains(response,second.full_name)
        self.assertEqual(response.context['new_employee_recommendation_count'],1)
        self.assertEqual(response.context['dispatcher_filter_camp_ids'],[self.camp.pk])

    @patch('apps.personnel.movement_recommendations.build')
    def test_new_employee_tab_keeps_arrival_and_departure_navigation_counts(self,build):
        build.return_value={'ARRIVAL':[object(),object()],'DEPARTURE':[object()]}
        self.client.force_login(self.dispatch)
        response=self.client.get(reverse('personnel:new_employee_arrivals'))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['arrival_recommendation_count'],2)
        self.assertEqual(response.context['departure_recommendation_count'],1)
        self.assertNotContains(response,'recruitment-section-tabs',html=False)
        self.assertContains(response,'Состав, назначения и графики')
        self.assertContains(response,'Мои заявки')
    def test_new_candidate_with_ready_date_can_be_opened_for_arrival_by_dispatcher(self):
        row=self.row();service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        candidate=service.save_candidate(self.recruiter,row.pk,{
            'pool_candidate':None,'full_name':'Новый кандидат','phone':'89990000000','status':'NEW',
            'ready_on':self.day,'notes':'','documents':[]},revision=0,files=[])
        self.client.force_login(self.dispatch)
        # Admin/dispatcher with recruitment access gets a direct, visible route into preparation.
        self.dispatch.role=Role.objects.get(code='ADMIN');self.dispatch.save(update_fields=['role'])
        page=self.client.get(reverse('personnel:recruitment_detail',args=[row.pk]))
        self.assertContains(page,'Как оформить заезд');self.assertContains(page,'Оформить заезд')
        dispatcher=self.client.get(reverse('personnel:housing'))
        self.assertContains(dispatcher,'Монтажник');self.assertContains(dispatcher,'Подбор по должности')
        self.assertContains(dispatcher,'Новый кандидат');self.assertContains(dispatcher,'Оформить заезд')
        self.assertContains(dispatcher,'Новые сотрудники');self.assertNotContains(dispatcher,'Рекомендации: заезд новых сотрудников')
        recommendations=self.client.get(reverse('personnel:new_employee_arrivals'))
        self.assertContains(recommendations,'Заезд новых сотрудников');self.assertContains(recommendations,'Новый кандидат')
        self.assertContains(recommendations,'Можно оформлять');self.assertContains(recommendations,'Оформить отдельно')
        response=self.client.post(reverse('personnel:recruitment_handoff',args=[candidate.pk]),{'revision':candidate.revision})
        prep=ArrivalPreparation.objects.get(candidate=candidate)
        self.assertRedirects(response,reverse('personnel:recruitment_arrival',args=[prep.pk]),fetch_redirect_response=False)
        arrival=self.client.get(reverse('personnel:recruitment_arrival',args=[prep.pk]))
        self.assertContains(arrival,'ФИО кандидата');self.assertContains(arrival,'value="Новый кандидат"')
        self.assertContains(arrival,'Проект');self.assertContains(arrival,f'value="{self.obj.name}"')
        self.assertNotContains(arrival,f'value="{self.project.name}"')
        self.assertContains(arrival,f'<option value="{self.camp.pk}" selected>')
        candidate.refresh_from_db();self.assertEqual(candidate.status,'TRANSFERRED')
    def test_arrival_preparations_cannot_exceed_requested_quantity(self):
        row=service.save_request(self.dispatch,self.values(quantity=1));first=self.candidate(row);service.handoff(self.recruiter,first.pk,first.revision)
        second=RecruitmentCandidate.objects.create(company=self.company,request=row,full_name='Резервный кандидат',
            status='READY',ready_on=self.day,created_by=self.recruiter)
        with self.assertRaisesMessage(ValidationError,'уже оформлено требуемое количество'):
            service.handoff(self.dispatch,second.pk,second.revision)
    def test_request_closes_when_required_employee_arrives(self):
        from .departure_workflow import arrive_camp
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        from .recruitment_forms import CandidateForm
        row=service.save_request(self.dispatch,self.values(quantity=1));candidate=self.candidate(row)
        pool=candidate.pool_candidate
        prep=service.handoff(self.recruiter,candidate.pk,candidate.revision)
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),workflow=True)
        movement=prep.movement_request;movement.status='EN_ROUTE';movement.hub_departed_on=self.day
        movement.save(update_fields=['status','hub_departed_on','updated_at'])
        admin=User.objects.create_user(username='arrival-completion-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        arrive_camp(admin,movement,self.day+timedelta(days=1));row.refresh_from_db()
        self.assertIsNotNone(row.completed_at)
        pool.refresh_from_db();self.assertFalse(pool.is_active)
        other_position=Position.objects.create(company=self.company,name='Другая должность',code='drugaya')
        another=service.save_request(self.dispatch,self.values(position_ref=other_position))
        self.assertNotIn(pool,CandidateForm(company=self.company,request_row=another).fields['pool_candidate'].queryset)
        self.client.force_login(self.dispatch)
        active=self.client.get(reverse('personnel:recruitment_requests'))
        self.assertNotContains(active,f'№{row.pk} · {row.construction_object}')
        completed=self.client.get(reverse('personnel:recruitment_requests'),{'state':'completed'})
        self.assertContains(completed,f'№{row.pk} ·');self.assertContains(completed,'Выполнена')
        with self.assertRaisesMessage(ValidationError,'Заявка выполнена'):
            service.handoff(self.dispatch,RecruitmentCandidate.objects.create(company=self.company,request=row,
                full_name='Резерв',status='READY',ready_on=self.day,created_by=self.recruiter).pk,0)
    def test_new_candidate_can_start_arrival_without_existing_employee_card(self):
        prep=self.preparation();values=self.arrival_values(employee=None)
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,values,workflow=True)
        self.assertIsNone(prep.employee_id);self.assertIsNone(prep.movement_request.person.employee_id)
        self.assertEqual(prep.movement_request.employment_kind,'NEW');self.assertEqual(prep.movement_request.phone,prep.candidate.phone)
    def test_common_pool_candidate_can_be_selected_for_vacancy(self):
        from .models import TalentPoolCandidate
        from .recruitment_forms import CandidateForm
        row=self.row();service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        pool=TalentPoolCandidate.objects.create(company=self.company,full_name='Человек из пула',phone='555',created_by=self.recruiter,
            source_data={'position':'Монтажник','construction_site':'КТП'})
        form=CandidateForm({'pool_candidate':pool.pk,'full_name':'','phone':'','status':'READY','ready_on':self.day,'notes':'','revision':0},company=self.company,request_row=row)
        self.assertEqual(form.initial['request_position'],row.position)
        self.assertEqual(form.fields['pool_candidate'].label_from_instance(pool),'Человек из пула — Монтажник · Новый · КТП')
        self.assertEqual(form.pool_candidate_data[str(pool.pk)],{'full_name':'Человек из пула','phone':'555','notes':''})
        self.assertTrue(form.is_valid(),form.errors)
        candidate=service.save_candidate(self.recruiter,row.pk,form.cleaned_data)
        self.assertEqual(candidate.pool_candidate,pool);self.assertEqual(candidate.full_name,pool.full_name)
        self.assertTrue(pool.vacancy_candidates.filter(request=row).exists())
        another=self.row();service.assign_request(self.hr_head,another.pk,self.recruiter,another.revision)
        second_form=CandidateForm(company=self.company,request_row=another)
        self.assertNotIn(pool,second_form.fields['pool_candidate'].queryset)
        with self.assertRaisesMessage(ValidationError,'уже участвует в другой действующей заявке'):
            service.save_candidate(self.recruiter,another.pk,{'pool_candidate':pool,'full_name':pool.full_name,
                'phone':pool.phone,'status':'READY','ready_on':self.day,'notes':''})
        self.client.force_login(self.recruiter)
        page=self.client.get(reverse('personnel:recruitment_candidate_create',args=[row.pk]))
        self.assertNotContains(page,f'<option value="{pool.pk}">')

    def test_security_approved_candidate_is_in_store_and_available_for_vacancy(self):
        from .models import TalentPoolCandidate
        from .recruitment_forms import CandidateForm
        pool=TalentPoolCandidate.objects.create(company=self.company,full_name='Согласованный СЭБ кандидат',
            stage='MEDICAL_DOCUMENTS',manager_approved=True,manager_approved_on=date(2026,11,20),
            security_approved=True,security_approved_on=date(2026,11,21),position=self.position,
            construction_object=self.obj,created_by=self.recruiter)
        vacancy=self.row();service.assign_request(self.hr_head,vacancy.pk,self.recruiter,vacancy.revision)
        self.assertIn(pool,CandidateForm(company=self.company,request_row=vacancy).fields['pool_candidate'].queryset)
        self.client.force_login(self.recruiter)
        page=self.client.get(reverse('personnel:recruitment_list'))
        groups={row['label']:row['count'] for row in page.context['funnel_groups']}
        self.assertEqual(groups['В работе'],0);self.assertEqual(groups['Магазин'],1)
        store=self.client.get(reverse('personnel:recruitment_list'),{'candidate_stage':'selected'})
        self.assertContains(store,pool.full_name)
        work=self.client.get(reverse('personnel:recruitment_list'),{'candidate_stage':'work'})
        self.assertNotContains(work,pool.full_name)
    def test_handoff_and_confirmation_are_idempotent(self):
        prep=self.preparation();c=prep.candidate
        self.assertEqual(service.handoff(self.recruiter,c.pk,0).pk,prep.pk)
        saved=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        again=service.prepare_arrival(self.dispatch,prep.pk,0,self.arrival_values(),confirm=True)
        self.assertEqual(saved.actual_stay_id,again.actual_stay_id);self.assertEqual(CampStay.objects.count(),1);self.assertEqual(HousingEvent.objects.count(),1)
    def test_cancelled_actual_stay_is_removed_from_arrived_and_can_be_corrected(self):
        prep=self.preparation();prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        CampStay.objects.filter(pk=prep.actual_stay_id).update(cancelled_at=timezone.now())
        self.assertEqual(service.progress(RecruitmentRequest.objects.get(pk=prep.candidate.request_id))['arrived'],0)
        prep.refresh_from_db();prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        self.assertTrue(prep.actually_arrived);self.assertEqual(CampStay.objects.filter(cancelled_at__isnull=True).count(),1)
    def test_cannot_arrive_without_explicit_employment_and_camp(self):
        prep=self.preparation()
        for fields in ({'employee':None},{'camp':None},{'actual_on':None}):
            self.assertValidation(service.prepare_arrival,self.dispatch,prep.pk,prep.revision,self.arrival_values(**fields),confirm=True)
        self.assertFalse(CampStay.objects.exists());self.assertFalse(Person.objects.exists())
    def test_inactive_employee_and_foreign_or_unlinked_camps_rejected(self):
        prep=self.preparation()
        for fields in ({'employee':self.foreign_employee},{'camp':self.foreign_camp},{'camp':self.unlinked}):
            self.assertValidation(service.prepare_arrival,self.dispatch,prep.pk,prep.revision,self.arrival_values(**fields),confirm=True)
        self.employee.is_active=False;self.employee.save()
        self.assertValidation(service.prepare_arrival,self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        self.assertFalse(CampStay.objects.exists())
    def test_edit_window_enforced_without_partial_changes(self):
        prep=self.preparation()
        with patch('apps.personnel.housing.require_fact_day',side_effect=ValidationError('День закрыт')):
            self.assertValidation(service.prepare_arrival,self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        self.assertFalse(Person.objects.exists());self.assertFalse(CampStay.objects.exists());prep.refresh_from_db();self.assertEqual(prep.status,'EXPECTED')
    def test_optimistic_revisions_reject_stale_request_candidate_and_arrival(self):
        row=self.row();service.save_request(self.dispatch,self.values(quantity=3),row.pk,row.revision)
        self.assertValidation(service.save_request,self.dispatch,self.values(quantity=4),row.pk,row.revision)
        c=self.candidate();values={'full_name':c.full_name,'phone':'','status':'READY','ready_on':self.day,'notes':''}
        service.save_candidate(self.recruiter,c.request_id,values,c.pk,c.revision)
        self.assertValidation(service.save_candidate,self.recruiter,c.request_id,values,c.pk,c.revision)
        c.refresh_from_db();prep=service.handoff(self.recruiter,c.pk,c.revision)
        service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values())
        self.assertValidation(service.prepare_arrival,self.dispatch,prep.pk,prep.revision,self.arrival_values())
    def test_duplicate_candidate_cannot_be_added_to_same_request(self):
        row=self.row();first=self.candidate(row)
        with self.assertRaisesMessage(ValidationError,'уже добавлен в эту заявку'):
            service.save_candidate(self.recruiter,row.pk,{'pool_candidate':None,'full_name':f'  {first.full_name.lower()}  ',
                'phone':first.phone,'status':'READY','ready_on':self.day,'notes':''})
        self.assertEqual(row.candidates.count(),1);self.assertEqual(row.candidates.get(),first)
    def test_only_assigned_recruiter_can_work_request(self):
        row=self.row();service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        row.refresh_from_db()
        self.assertValidation(service.save_candidate,self.recruiter2,row.pk,{'full_name':'Чужой','phone':'','status':'NEW','ready_on':None,'notes':''})
        self.assertFalse(RecruitmentCandidate.objects.exists())
    def test_hr_head_assigns_and_reassigns_responsible_recruiter(self):
        from .models import RecruitmentEvent
        row=self.row();url=reverse('personnel:recruitment_detail',args=[row.pk])
        self.client.force_login(self.recruiter)
        page=self.client.get(url)
        self.assertNotContains(page,'Взять в работу');self.assertNotContains(page,'name="recruiter"',html=False)
        self.assertEqual(self.client.post(url,{'action':'assign','revision':row.revision,'recruiter':self.recruiter.pk}).status_code,403)
        self.client.force_login(self.hr_head)
        page=self.client.get(url)
        self.assertContains(page,'Ответственный рекрутер');self.assertContains(page,'name="recruiter"',html=False)
        response=self.client.post(url,{'action':'assign','revision':row.revision,'recruiter':self.recruiter.pk})
        self.assertRedirects(response,url);row.refresh_from_db();self.assertEqual(row.assigned_to,self.recruiter)
        self.assertTrue(RecruitmentEvent.objects.filter(request=row,description__contains='Назначен ответственный рекрутер').exists())
        candidate_create_url=reverse('personnel:recruitment_candidate_create',args=[row.pk])
        self.client.force_login(self.recruiter);self.assertContains(self.client.get(url),candidate_create_url)
        self.client.force_login(self.hr_head)
        self.client.post(url,{'action':'assign','revision':row.revision,'recruiter':self.recruiter2.pk})
        row.refresh_from_db();self.assertEqual(row.assigned_to,self.recruiter2)
        self.assertTrue(RecruitmentEvent.objects.filter(request=row,description__contains='Ответственный рекрутер изменён').exists())
        self.client.force_login(self.recruiter);self.assertNotContains(self.client.get(url),candidate_create_url)
    def test_role_separation_in_services(self):
        with self.assertRaises(PermissionDenied):service.save_request(self.recruiter,self.values())
        row=self.row()
        with self.assertRaises(PermissionDenied):service.assign_request(self.dispatch,row.pk,self.recruiter,row.revision)
        prep=self.preparation()
        with self.assertRaises(PermissionDenied):service.prepare_arrival(self.recruiter,prep.pk,prep.revision,self.arrival_values(),confirm=True)
    def test_readiness_required_and_handed_candidate_immutable(self):
        c=self.candidate(status='SCREENING',ready_on=None)
        self.assertValidation(service.handoff,self.recruiter,c.pk,c.revision)
        c2=self.candidate();service.handoff(self.recruiter,c2.pk,c2.revision)
        self.assertValidation(service.save_candidate,self.recruiter,c2.request_id,{'full_name':'Подмена','phone':'','status':'READY','ready_on':self.day,'notes':''},c2.pk,c2.revision)
    def test_request_cancel_keeps_actual_arrivals_and_cancels_expected(self):
        prep=self.preparation();row=prep.candidate.request
        service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        c=service.save_candidate(self.recruiter,row.pk,{'full_name':'Второй','phone':'','status':'READY','ready_on':self.day,'notes':''})
        waiting=service.handoff(self.recruiter,c.pk,c.revision);row.refresh_from_db()
        service.cancel_request(self.dispatch,row.pk,row.revision,'Потребность закрыта')
        waiting.refresh_from_db();self.assertEqual(waiting.status,'CANCELLED');self.assertEqual(CampStay.objects.filter(cancelled_at__isnull=True).count(),1)
        row.refresh_from_db();self.assertEqual(service.progress(row)['arrived'],1)
        self.assertValidation(service.save_candidate,self.recruiter,row.pk,{'full_name':'Лишний','phone':'','status':'NEW','ready_on':None,'notes':''})
    def test_cancel_preparation_and_rejected_candidates_not_ready(self):
        prep=self.preparation();service.cancel_preparation(self.dispatch,prep.pk,prep.revision,'Отказался')
        row=RecruitmentRequest.objects.get(pk=prep.candidate.request_id)
        self.assertEqual(service.progress(row)['ready'],0);self.assertEqual(service.progress(row)['arrived'],0)
        self.assertValidation(service.prepare_arrival,self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
    def test_same_employee_cannot_be_in_two_expected_arrivals(self):
        a=self.preparation();b=self.preparation()
        service.prepare_arrival(self.dispatch,a.pk,a.revision,self.arrival_values())
        self.assertValidation(service.prepare_arrival,self.dispatch,b.pk,b.revision,self.arrival_values())
    def test_macro_object_supports_child_camps(self):
        parent=ConstructionObject.objects.create(company=self.company,project=self.project,name='Общий объект')
        self.obj.parent=parent;self.obj.save()
        self.assertIn(self.camp,service.allowed_camps(self.company,parent));self.assertNotIn(self.foreign_camp,service.allowed_camps(self.company,parent))
    def test_cannot_change_destination_after_handoff(self):
        prep=self.preparation();row=prep.candidate.request
        other=ConstructionObject.objects.create(company=self.company,project=self.project,name='Другой')
        row.refresh_from_db();self.assertValidation(service.save_request,self.dispatch,self.values(construction_object=other),row.pk,row.revision)
    def test_foreign_request_rejected_at_service_and_model_boundary(self):
        foreign_obj=ConstructionObject.objects.create(company=self.other,project=Project.objects.create(company=self.other,name='Чужой'),name='Чужой')
        self.assertValidation(service.save_request,self.dispatch,self.values(construction_object=foreign_obj))
        row=self.row()
        with self.assertRaises(RecruitmentRequest.DoesNotExist):service.assign_request(self.foreign_hr_head,row.pk,self.foreign,row.revision)
    def test_private_documents_download_scope_and_upload_validation(self):
        row=self.row();service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        values={'full_name':'С резюме','phone':'','status':'NEW','ready_on':None,'notes':''}
        with TemporaryDirectory() as root,override_settings(ACTUATION_FILES_ROOT=root):
            c=service.save_candidate(self.recruiter,row.pk,values,files=[SimpleUploadedFile('resume.pdf',b'%PDF-private'),SimpleUploadedFile('info.txt',b'info')])
            self.assertEqual(c.documents.count(),2);doc=c.documents.first()
            self.assertTrue(Path(doc.file.path).is_relative_to(Path(root)/'recruitment'))
            self.client.force_login(self.dispatch);response=self.client.get(reverse('personnel:recruitment_document',args=[doc.pk]))
            self.assertEqual(b''.join(response.streaming_content),b'%PDF-private');self.assertIn('attachment',response['Content-Disposition']);self.assertEqual(response['Cache-Control'],'private, no-store')
            self.client.force_login(self.foreign);self.assertEqual(self.client.get(reverse('personnel:recruitment_document',args=[doc.pk])).status_code,404)
            self.assertValidation(service.save_candidate,self.recruiter,row.pk,values,files=[SimpleUploadedFile('run.exe',b'x')])
            self.assertEqual(RecruitmentCandidate.objects.count(),1)
    def test_upload_failure_cleans_files_and_rolls_back_candidate(self):
        row=self.row();service.assign_request(self.hr_head,row.pk,self.recruiter,row.revision)
        with TemporaryDirectory() as root,override_settings(ACTUATION_FILES_ROOT=root):
            with patch.object(CandidateDocument,'save',side_effect=ValidationError('Отказ')):
                self.assertValidation(service.save_candidate,self.recruiter,row.pk,{'full_name':'X','phone':'','status':'NEW','ready_on':None,'notes':''},files=[SimpleUploadedFile('a.pdf',b'x')])
            self.assertFalse(RecruitmentCandidate.objects.exists());self.assertFalse([p for p in Path(root).rglob('*') if p.is_file()])
    def test_three_actual_sections_and_request_prefill(self):
        self.client.force_login(self.dispatch)
        for route,title in [('housing','Диспетчерская'),('movements','Новая заявка'),('recruitment_list','Банк сотрудников')]:
            page=self.client.get(reverse('personnel:'+route));self.assertNotContains(page,'data-personnel-sections');self.assertContains(page,title)
        page=self.client.get(reverse('personnel:housing'));self.assertNotContains(page,'data-movement-form')
        page=self.client.get(reverse('personnel:movements'));self.assertContains(page,'data-movement-form');self.assertNotContains(page,'data-roster-grid')
        welder=Position.objects.create(company=self.company,name='Сварщик',code='svarshchik')
        page=self.client.get(reverse('personnel:recruitment_create'),{'position':'Сварщик','construction_object':self.obj.pk})
        self.assertEqual(page.context['form'].initial['position_ref'],welder.pk);self.assertEqual(int(page.context['form'].initial['construction_object']),self.obj.pk)
        self.assertContains(page,'<select name="position_ref"',html=False)
    def test_recruiter_landing_menu_and_direct_route_restrictions(self):
        self.client.force_login(self.recruiter)
        self.assertRedirects(self.client.get(reverse('dashboard:index')),reverse('personnel:recruitment_list'))
        page=self.client.get(reverse('personnel:recruitment_list'));self.assertContains(page,'Банк сотрудников');self.assertNotContains(page,'data-paths="/personnel/" hidden')
        self.assertEqual(self.client.get(reverse('personnel:recruitment_requests')).status_code,200)
        sidebar=page.content.decode().split('<aside class="sidebar"',1)[1].split('</aside>',1)[0]
        for text in ('Справочники','Внесение факта','Обзор','Мои задания'):self.assertNotIn(text,sidebar)
        for route in ['personnel:housing','personnel:movements','resources:employee_list','production:fact_day_workspace','accounts:user_list','planning:workspace_list','personnel:recruitment_create']:
            self.assertEqual(self.client.get(reverse(route)).status_code,403,route)
        self.assertEqual(self.client.get(reverse('instructions:index')).status_code,200)
    def test_dispatcher_cannot_edit_candidates_and_recruiter_cannot_edit_need(self):
        row=self.row();self.client.force_login(self.dispatch)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_candidate_create',args=[row.pk])).status_code,403)
        self.client.force_login(self.recruiter)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_edit',args=[row.pk])).status_code,403)
    def test_http_company_scope_read_and_mutations(self):
        c=self.candidate();self.client.force_login(self.foreign)
        for route,args,method,data in [
            ('recruitment_detail',[c.request_id],'get',{}),('recruitment_handoff',[c.pk],'post',{'revision':c.revision}),
            ('recruitment_candidate_edit',[c.request_id,c.pk],'get',{})]:
            self.assertEqual(getattr(self.client,method)(reverse('personnel:'+route,args=args),data).status_code,404)
    def test_http_request_candidate_handoff_arrival(self):
        self.client.force_login(self.dispatch)
        response=self.client.post(reverse('personnel:recruitment_create'),{'construction_object':self.obj.pk,'position_ref':self.position.pk,'quantity':1,'required_on':str(self.day),'requirements':'','revision':0})
        row=RecruitmentRequest.objects.get();self.assertRedirects(response,reverse('personnel:recruitment_detail',args=[row.pk]))
        self.client.force_login(self.hr_head)
        self.client.post(reverse('personnel:recruitment_detail',args=[row.pk]),{'action':'assign','recruiter':self.recruiter.pk,'revision':row.revision})
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_candidate_create',args=[row.pk]),{'full_name':'Новый кандидат','phone':'','status':'READY','ready_on':str(self.day),'notes':'','revision':0})
        self.assertEqual(response.status_code,302);c=RecruitmentCandidate.objects.get()
        self.client.post(reverse('personnel:recruitment_handoff',args=[c.pk]),{'revision':c.revision})
        prep=ArrivalPreparation.objects.get();self.client.force_login(self.dispatch)
        response=self.client.post(reverse('personnel:recruitment_arrival',args=[prep.pk]),{'revision':prep.revision,'expected_on':str(self.day),'camp':self.camp.pk,'employee':self.employee.pk,'actual_on':str(self.day),'notes':'','action':'confirm'})
        self.assertRedirects(response,reverse('personnel:movements'));prep.refresh_from_db();self.assertTrue(prep.actually_arrived)
        row.refresh_from_db();self.assertEqual(service.progress(row)['label'],'Выполнена')
    def test_queue_includes_expected_not_confirmed_or_cancelled(self):
        prep=self.preparation();self.client.force_login(self.dispatch)
        page=self.client.get(reverse('personnel:movements'));self.assertEqual(page.context['arrival_queue'].paginator.count,1)
        service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        page=self.client.get(reverse('personnel:movements'));self.assertEqual(page.context['arrival_queue'].paginator.count,0)
    def test_mutations_require_post_and_csrf(self):
        from django.test import Client
        c=self.candidate();self.client.force_login(self.recruiter)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_handoff',args=[c.pk])).status_code,405)
        client=Client(enforce_csrf_checks=True);client.force_login(self.recruiter)
        self.assertEqual(client.post(reverse('personnel:recruitment_handoff',args=[c.pk]),{'revision':c.revision}).status_code,403)

    def test_existing_matching_mass_arrival_can_be_explicitly_confirmed(self):
        from . import housing
        prep=self.preparation()
        housing.move(self.dispatch,'ARRIVAL',self.camp,self.day,[],employees=[self.employee])
        stay=CampStay.objects.get()
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        self.assertEqual(prep.actual_stay_id,stay.pk);self.assertEqual(HousingEvent.objects.count(),1)
    def test_one_actual_arrival_cannot_confirm_two_different_candidates(self):
        first=self.preparation();second=self.preparation()
        service.prepare_arrival(self.dispatch,first.pk,first.revision,self.arrival_values(),confirm=True)
        self.assertValidation(service.prepare_arrival,self.dispatch,second.pk,second.revision,self.arrival_values(),confirm=True)
        self.assertEqual(CampStay.objects.count(),1)
    def test_unassigned_recruiter_edit_is_403_and_not_server_error(self):
        row=self.row();self.client.force_login(self.recruiter)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_candidate_create',args=[row.pk])).status_code,403)
    def test_recruitment_roles_available_to_admin_account_and_access_group_forms(self):
        from apps.accounts.access_groups import GroupForm
        from apps.accounts.forms import CompanyUserAccessForm
        recruiter=Role.objects.get(code='RECRUITER');head=Role.objects.get(code='RECRUITMENT_HEAD')
        self.assertEqual(recruiter.name,'Рекрутер');self.assertEqual(head.name,'Руководитель банка сотрудников')
        for form in (GroupForm(company=self.company),CompanyUserAccessForm(company=self.company)):
            self.assertIn(recruiter,form.fields['role'].queryset);self.assertIn(head,form.fields['role'].queryset)

    def test_recruitment_head_assigns_recruiter_and_manages_the_bank(self):
        row=self.row();self.client.force_login(self.recruitment_head)
        detail=self.client.get(reverse('personnel:recruitment_detail',args=[row.pk]))
        self.assertEqual(detail.status_code,200);self.assertTrue(detail.context['can_assign_recruiter']);self.assertTrue(detail.context['can_manage_candidates'])
        assigned=self.client.post(reverse('personnel:recruitment_detail',args=[row.pk]),{
            'action':'assign','revision':row.revision,'recruiter':self.recruiter.pk})
        self.assertRedirects(assigned,reverse('personnel:recruitment_detail',args=[row.pk]))
        row.refresh_from_db();self.assertEqual(row.assigned_to,self.recruiter)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_candidate_create',args=[row.pk])).status_code,200)
        specialists=self.client.get(reverse('personnel:recruitment_specialists'))
        self.assertContains(specialists,'Добавить рекрутера')
        self.assertEqual(self.client.get(reverse('personnel:recruitment_specialist_create')).status_code,200)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_create')).status_code,403)
    def test_candidate_multiple_upload_field_and_size_rejection(self):
        from .recruitment_forms import CandidateForm
        row=self.row()
        form=CandidateForm({'full_name':'Кандидат','phone':'','status':'NEW','ready_on':'','notes':'','revision':0},
            {'documents':[SimpleUploadedFile('a.pdf',b'x'),SimpleUploadedFile('b.txt',b'y')]},company=self.company,request_row=row)
        self.assertTrue(form.is_valid(),form.errors);self.assertEqual(len(form.cleaned_data['documents']),2)
        large=SimpleUploadedFile('a.pdf',b'x');large.size=11*1024*1024
        self.assertValidation(service.validate_documents,[large])
    def test_cancelled_request_removed_from_queue_and_progress_excludes_decline(self):
        prep=self.preparation();service.cancel_preparation(self.dispatch,prep.pk,prep.revision,'Отказался')
        row=RecruitmentRequest.objects.get(pk=prep.candidate.request_id)
        self.assertEqual(service.progress(row)['shortlisted'],0)
        row=self.row();c=self.candidate(row);service.handoff(self.recruiter,c.pk,c.revision);row.refresh_from_db()
        service.cancel_request(self.dispatch,row.pk,row.revision,'Отмена потребности')
        self.client.force_login(self.dispatch);page=self.client.get(reverse('personnel:movements'))
        self.assertEqual(page.context['arrival_queue'].paginator.count,0)

    def test_store_2026_import_creates_and_idempotently_updates_pool(self):
        from openpyxl import Workbook
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        from .talent_pool_import import HEADERS,import_sheet
        workbook=Workbook();sheet=workbook.active;sheet.title='Магазин 2026';sheet.append(HEADERS)
        sheet.append([1,'  Иванов   Иван Иванович ','Монтажник (КТП)','КТП',None,'1234 567890',89991234567,None,None,None,None,None,'Уфа','готов',None,'Петров',None,'Комментарий','Иванов Иван Иванович'])
        payload=BytesIO();workbook.save(payload);payload.seek(0)
        self.assertEqual(import_sheet(self.recruiter,payload),{'created':1,'updated':0,'skipped':0})
        row=TalentPoolCandidate.objects.get();self.assertEqual(row.full_name,'Иванов Иван Иванович');self.assertEqual(row.phone,'89991234567')
        self.assertEqual(row.stage,'SELECTED')
        self.assertEqual(row.responsible_hr.name,'Петров')
        self.assertEqual(RecruitmentSpecialist.objects.count(),1)
        self.assertEqual(row.position,self.position);self.assertEqual(row.construction_object,self.obj);self.assertEqual(row.base_city,self.base_city)
        self.assertEqual(row.source_data['position'],'Монтажник (КТП)');self.assertEqual(row.source_data['base_city'],'Уфа')
        payload.seek(0);self.assertEqual(import_sheet(self.recruiter,payload),{'created':0,'updated':0,'skipped':1})
        self.client.force_login(self.recruiter);page=self.client.get(reverse('personnel:recruitment_pool'))
        self.assertContains(page,'Монтажник');self.assertContains(page,'КТП');self.assertContains(page,'Уфа')
        self.assertContains(page,reverse('personnel:recruitment_pool_import'))
    def test_all_candidates_is_paginated_and_filters_are_kept_in_page_links(self):
        from .models import TalentPoolCandidate
        TalentPoolCandidate.objects.bulk_create([
            TalentPoolCandidate(company=self.company,full_name=f'Кандидат страницы {number:03d}',phone=f'900{number:03d}',
                stage='NEW',created_by=self.recruiter)
            for number in range(1,56)
        ])
        self.client.force_login(self.recruiter);url=reverse('personnel:recruitment_pool')
        first=self.client.get(url,{'q':'Кандидат страницы','stage':'NEW'})
        self.assertEqual(first.context['page'].paginator.count,55)
        self.assertEqual(len(first.context['rows']),50)
        self.assertContains(first,'Страница 1 из 2')
        self.assertContains(first,'stage=NEW&amp;page=2',html=False)
        second=self.client.get(url,{'q':'Кандидат страницы','stage':'NEW','page':2})
        self.assertEqual(len(second.context['rows']),5)
        self.assertEqual(second.context['rows'][0].full_name,'Кандидат страницы 051')
        self.assertContains(second,'Страница 2 из 2')
    def test_funnel_positions_are_paginated_without_hiding_candidate_cards(self):
        from .models import TalentPoolCandidate
        positions=Position.objects.bulk_create([
            Position(company=self.company,name=f'Должность страницы {number:03d}',code=f'page-{number:03d}')
            for number in range(1,31)
        ])
        TalentPoolCandidate.objects.bulk_create([
            TalentPoolCandidate(company=self.company,full_name=f'Сотрудник страницы {number:03d}',position=position,
                construction_object=self.obj,stage='NEW',created_by=self.recruiter)
            for number,position in enumerate(positions,1)
        ])
        self.client.force_login(self.recruiter);url=reverse('personnel:recruitment_list')
        first=self.client.get(url)
        self.assertEqual(first.context['position_page'].paginator.count,30)
        self.assertEqual(len(first.context['position_summary']),25)
        self.assertContains(first,'Страница 1 из 2')
        self.assertContains(first,'data-candidate-inline-target',html=False)
        second=self.client.get(url,{'position_page':2})
        self.assertEqual(len(second.context['position_summary']),5)
        self.assertEqual(second.context['position_summary'][0]['position'],'Должность страницы 026')
        self.assertContains(second,'Страница 2 из 2')
        self.assertContains(second,'Сотрудник страницы 026')
    def test_recruiter_directory_is_paginated_and_preserves_filters(self):
        from .models import RecruitmentSpecialist
        RecruitmentSpecialist.objects.bulk_create([
            RecruitmentSpecialist(company=self.company,name=f'Рекрутер страницы {number:03d}')
            for number in range(1,56)
        ])
        self.client.force_login(self.hr_head);url=reverse('personnel:recruitment_specialists')
        first=self.client.get(url,{'linked':'no','sort':'candidates'})
        self.assertEqual(first.context['page'].paginator.count,55)
        self.assertEqual(len(first.context['rows']),50)
        self.assertContains(first,'Страница 1 из 2')
        self.assertContains(first,'sort=candidates&amp;page=2',html=False)
        second=self.client.get(url,{'linked':'no','sort':'candidates','page':2})
        self.assertEqual(len(second.context['rows']),5)
        self.assertContains(second,'Страница 2 из 2')
    def test_import_discards_dates_and_excel_errors_from_responsible_hr(self):
        from datetime import date
        from openpyxl import Workbook
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        from .talent_pool_import import HEADERS,import_sheet
        workbook=Workbook();sheet=workbook.active;sheet.title='Магазин 2026';sheet.append(HEADERS)
        for number,name,responsible in ((1,'Кандидат с датой',date(2026,1,30)),(2,'Кандидат с ошибкой','#REF!'),
                (3,'Кандидат со статусом','готов после 14.12'),(4,'Кандидат с отказом','КАНДИДАТ ОТКАЗАЛСЯ')):
            values=['']*len(HEADERS)
            values[0]=number;values[1]=name;values[2]='Монтажник';values[3]='КТП';values[15]=responsible
            sheet.append(values)
        payload=BytesIO();workbook.save(payload);payload.seek(0)
        self.assertEqual(import_sheet(self.recruiter,payload),{'created':4,'updated':0,'skipped':0})
        self.assertFalse(RecruitmentSpecialist.objects.exists())
        for candidate in TalentPoolCandidate.objects.all():
            self.assertIsNone(candidate.responsible_hr)
            self.assertEqual(candidate.source_data['responsible_hr'],'')
            self.assertEqual(candidate.source_data['funnel_sources'][0]['data']['responsible_hr'],'')
    def test_cleanup_migration_removes_existing_invalid_responsible_hr_values(self):
        from importlib import import_module
        from django.apps import apps
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        specialist=RecruitmentSpecialist(company=self.company,name='2026-02-12')
        RecruitmentSpecialist.objects.bulk_create([specialist])
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат с мусором',
            responsible_hr=specialist,created_by=self.recruiter,
            source_data={'responsible_hr':'2026-02-12','funnel_sources':[{'data':{'responsible_hr':'#REF!'}}]})
        migration=import_module('apps.personnel.migrations.0050_cleanup_invalid_recruitment_specialists')
        migration.clean_invalid_specialists(apps,None)
        candidate.refresh_from_db()
        self.assertIsNone(candidate.responsible_hr)
        self.assertEqual(candidate.source_data['responsible_hr'],'')
        self.assertEqual(candidate.source_data['funnel_sources'][0]['data']['responsible_hr'],'')
        self.assertFalse(RecruitmentSpecialist.objects.exists())

    def test_pass_date_migration_backfills_flags_and_ready_stage(self):
        from importlib import import_module
        from django.apps import apps
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат со старым пропуском',
            stage='PASS_PROCESSING',manager_approved=True,manager_approved_on=date(2026,11,10),
            security_approved=True,security_approved_on=date(2026,11,11),medical_exam_passed=True,
            medical_exam_on=date(2026,11,12),created_by=self.recruiter,
            source_data={'pass_submitted':'2026-11-13','pass_received':'2026-11-20'})
        migration=import_module('apps.personnel.migrations.0063_recruitment_pass_dates')
        migration.backfill_pass_dates(apps,None)
        candidate.refresh_from_db()
        self.assertTrue(candidate.pass_documents_sent);self.assertEqual(candidate.pass_documents_sent_on,date(2026,11,13))
        self.assertTrue(candidate.pass_ready);self.assertEqual(candidate.pass_ready_on,date(2026,11,20))
        self.assertEqual(candidate.stage,'READY');self.assertTrue(candidate.ready_for_arrival)
        self.assertEqual(candidate.ready_for_arrival_on,date(2026,11,20))
    def test_directory_cleanup_deactivates_statuses_and_matches_position_base(self):
        from importlib import import_module
        from django.apps import apps
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        specialist=RecruitmentSpecialist(company=self.company,name='готов после увольнения')
        RecruitmentSpecialist.objects.bulk_create([specialist])
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат для очистки',
            responsible_hr=specialist,created_by=self.recruiter,source_data={'position':'Монтажник (КТП)'})
        migration=import_module('apps.personnel.migrations.0053_recruitment_directory_cleanup')
        migration.clean_directories(apps,None)
        specialist.refresh_from_db();candidate.refresh_from_db()
        self.assertFalse(specialist.is_active)
        self.assertEqual(candidate.responsible_hr_id,specialist.pk)
        self.assertEqual(candidate.position_id,self.position.pk)
    def test_directory_cleanup_merges_case_only_recruiter_duplicates(self):
        from importlib import import_module
        from django.apps import apps
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        first=RecruitmentSpecialist.objects.create(company=self.company,name='Дарьина')
        duplicate=RecruitmentSpecialist.objects.create(company=self.company,name='дарьина',user=self.recruiter)
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат с дублем',responsible_hr=duplicate,
            created_by=self.recruiter)
        migration=import_module('apps.personnel.migrations.0053_recruitment_directory_cleanup')
        migration.clean_directories(apps,None)
        first.refresh_from_db();candidate.refresh_from_db()
        self.assertFalse(RecruitmentSpecialist.objects.filter(pk=duplicate.pk).exists())
        self.assertEqual(first.user_id,self.recruiter.pk)
        self.assertEqual(candidate.responsible_hr_id,first.pk)
    def test_workbook_import_unifies_all_funnel_sheets_and_history(self):
        from datetime import datetime
        from openpyxl import Workbook
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        from .talent_pool_import import HEADERS,PLAN_KEYS,import_sheet
        workbook=Workbook();store=workbook.active;store.title='Магазин 2026';store.append(HEADERS)
        store.append([1,'Единый Кандидат','Монтажник','КТП','1990-01-01','1234 567890','89990000001','Согласован',
            '2026-08-20','',datetime(2026,8,21),'2026-08-21','Уфа','готов','','Рекрутер','','','Единый Кандидат'])
        plan=workbook.create_sheet('План найма');plan.append([]);plan.append([]);plan.append([]);plan.append([])
        plan_headers=[f'Колонка {index}' for index in range(len(PLAN_KEYS))];plan_headers[7]='Должность';plan_headers[13]='ФИО кандидата';plan.append(plan_headers)
        plan_row=['']*len(PLAN_KEYS);plan_row[2]='КТП';plan_row[7]='Монтажник';plan_row[13]='Единый Кандидат'
        plan_row[14]='89990000001';plan_row[26]=datetime(2026,8,20);plan_row[29]=datetime(2026,8,22);plan.append(plan_row)
        transferred=workbook.create_sheet('Поставлены на заезд 2026');transferred.append(HEADERS)
        transferred.append([1,'Единый Кандидат','Монтажник','КТП','1990-01-01','1234 567890','89990000001','Согласован',
            '2026-08-20','',datetime(2026,8,21),datetime(2026,8,23),'Уфа','готов','','Рекрутер','','','Единый Кандидат'])
        rejected=workbook.create_sheet('Отказники');rejected.append(['№','ФИО'])
        rejected.append([1,'Другой Кандидат','Водитель','89990000002','Руководитель',datetime(2026,8,19),'',
            datetime(2026,8,20),datetime(2026,8,21),'','Омск','Не прошёл проверку','','Рекрутер','','Отказ работодателя'])
        payload=BytesIO();workbook.save(payload);payload.seek(0)
        self.assertEqual(import_sheet(self.recruiter,payload),{'created':2,'updated':0,'skipped':0})
        unified=TalentPoolCandidate.objects.get(full_name='Единый Кандидат')
        self.assertEqual(unified.stage,'TRANSFERRED');self.assertEqual(len(unified.source_data['funnel_sources']),3)
        self.assertEqual(unified.responsible_hr.name,'Рекрутер');self.assertEqual(RecruitmentSpecialist.objects.count(),1)
        self.assertEqual(unified.position,self.position);self.assertEqual(unified.construction_object,self.obj);self.assertEqual(unified.base_city,self.base_city)
        self.assertTrue(unified.security_approved);self.assertTrue(unified.medical_exam_passed)
        self.assertEqual(TalentPoolCandidate.objects.get(full_name='Другой Кандидат').stage,'REJECTED')
        payload.seek(0);self.assertEqual(import_sheet(self.recruiter,payload),{'created':0,'updated':0,'skipped':2})
        self.client.force_login(self.recruiter)
        card=self.client.get(reverse('personnel:recruitment_candidate_card',args=[unified.pk]))
        self.assertContains(card,'План найма');self.assertContains(card,'Поставлены на заезд 2026')
        self.assertContains(card,'Excel · строка 6');self.assertContains(card,'Поставлен на заезд')
    def test_shop_shows_full_funnel_and_unified_candidate_card(self):
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None)
        pool=candidate.pool_candidate
        self.client.force_login(self.recruiter)
        shop=self.client.get(reverse('personnel:recruitment_list'))
        self.assertContains(shop,'Банк сотрудников');self.assertContains(shop,'Кандидаты по этапам')
        self.assertNotContains(shop,'План найма · заявки на подбор')
        self.assertContains(shop,reverse('personnel:recruitment_requests'))
        self.assertNotContains(shop,'href="#recruitment-requests"')
        self.assertContains(shop,reverse('personnel:recruitment_pool_create'))
        self.assertContains(shop,'Добавить кандидата')
        self.assertContains(shop,'Направлен на согласование Заказчику');self.assertContains(shop,reverse('personnel:recruitment_candidate_card',args=[pool.pk]))
        self.assertContains(shop,reverse('personnel:recruitment_candidate_inline',args=[pool.pk]))
        self.assertNotContains(shop,'data-candidate-row=',html=False)
        requests=self.client.get(reverse('personnel:recruitment_requests'))
        self.assertContains(requests,'Заявки на подбор');self.assertContains(requests,'План найма · заявки на подбор')
        self.assertNotContains(requests,'Кандидаты по этапам')
        self.assertNotContains(requests,'id="recruitment-requests"')
        card=self.client.get(reverse('personnel:recruitment_candidate_card',args=[pool.pk]))
        self.assertContains(card,pool.full_name);self.assertContains(card,'История воронки')
        self.assertContains(card,f'Заявка №{candidate.request_id}');self.assertContains(card,'Кандидат: Направлен на согласование Заказчику.')
        rejected=self.client.get(reverse('personnel:recruitment_list'),{'candidate_stage':'rejected'})
        self.assertNotContains(rejected,pool.full_name)
        self.assertContains(rejected,'recruitment-funnel-card active',count=1,html=False)

    def test_recruitment_tabs_show_available_workspaces_and_actions(self):
        self.client.force_login(self.hr_head)
        page=self.client.get(reverse('personnel:recruitment_list'))
        for label in ('Заявки на подбор','Воронка кандидатов','Готовы к заезду','Все кандидаты','Рекрутеры','Добавить кандидата','Создать заявку'):
            self.assertContains(page,label)
        self.assertContains(page,'class="recruitment-section-actions"',html=False)
        self.assertNotContains(page,'recruitment-section-tab-action',html=False)
        self.assertContains(page,reverse('personnel:recruitment_ready'))
        self.assertNotContains(page,f'href="{reverse("personnel:new_employee_arrivals")}"',html=False)
        self.assertContains(page,reverse('personnel:recruitment_requests'))
        self.client.force_login(self.recruiter)
        recruiter_page=self.client.get(reverse('personnel:recruitment_list'))
        self.assertContains(recruiter_page,'Все кандидаты')
        self.assertContains(recruiter_page,reverse('personnel:recruitment_ready'))
        self.assertNotContains(recruiter_page,'Создать заявку')

    def test_admin_can_archive_restore_and_bulk_delete_unlinked_pool_cards(self):
        from .models import TalentPoolCandidate
        free=TalentPoolCandidate.objects.create(company=self.company,full_name='Свободная карточка для удаления',
            position=self.position,construction_object=self.obj,created_by=self.recruiter)
        linked=self.candidate().pool_candidate
        foreign=TalentPoolCandidate.objects.create(company=self.other,full_name='Чужая карточка для удаления',created_by=self.foreign)
        bulk_url=reverse('personnel:recruitment_pool_bulk_action')
        self.client.force_login(self.recruiter)
        recruiter_page=self.client.get(reverse('personnel:recruitment_list'))
        self.assertNotContains(recruiter_page,bulk_url);self.assertNotContains(recruiter_page,'Удалить выбранных')
        self.assertEqual(self.client.post(bulk_url,{'action':'archive','selected':[free.pk]}).status_code,403)
        self.client.force_login(self.admin)
        page=self.client.get(reverse('personnel:recruitment_list'))
        self.assertContains(page,bulk_url);self.assertContains(page,'Удалить выбранных');self.assertContains(page,'В архив')
        self.assertContains(page,'id="pool-admin-selected-count"',html=False)
        linked.stage='TRANSFERRED';linked.save(update_fields=['stage','updated_at'])
        ready_page=self.client.get(reverse('personnel:recruitment_ready'))
        self.assertNotContains(ready_page,bulk_url)
        self.assertNotContains(ready_page,'Выбрать все показанные карточки')
        self.assertNotContains(ready_page,'Выбрать для действий администратора')
        self.assertContains(ready_page,'Выбрать для заезда')
        self.assertContains(ready_page,'id="ready-arrival-selected-count"',html=False)
        ready_inline=self.client.get(reverse('personnel:recruitment_candidate_inline',args=[linked.pk]),{'source':'ready'})
        self.assertNotContains(ready_inline,bulk_url)
        bank_inline=self.client.get(reverse('personnel:recruitment_candidate_inline',args=[linked.pk]),{'source':'bank'})
        self.assertContains(bank_inline,bulk_url)
        archived=self.client.post(bulk_url,{'action':'archive','selected':[free.pk,linked.pk]})
        self.assertRedirects(archived,reverse('personnel:recruitment_list'),fetch_redirect_response=False)
        free.refresh_from_db();linked.refresh_from_db()
        self.assertIsNotNone(free.archived_at);self.assertEqual(free.archived_by,self.admin);self.assertFalse(free.is_active)
        self.assertIsNotNone(linked.archived_at);self.assertFalse(linked.is_active)
        active_page=self.client.get(reverse('personnel:recruitment_list'))
        self.assertNotContains(active_page,free.full_name);self.assertNotContains(active_page,linked.full_name)
        archive_page=self.client.get(reverse('personnel:recruitment_list'),{'pool_state':'archived'})
        self.assertContains(archive_page,free.full_name);self.assertContains(archive_page,linked.full_name)
        self.assertContains(archive_page,'Восстановить из архива')
        restored=self.client.post(bulk_url,{'action':'restore','selected':[free.pk],'return_state':'archived'})
        self.assertRedirects(restored,reverse('personnel:recruitment_list')+'?pool_state=archived',fetch_redirect_response=False)
        free.refresh_from_db();self.assertIsNone(free.archived_at);self.assertTrue(free.is_active)
        deleted=self.client.post(bulk_url,{'action':'delete','selected':[free.pk,linked.pk]},follow=True)
        self.assertFalse(TalentPoolCandidate.objects.filter(pk=free.pk).exists())
        self.assertTrue(TalentPoolCandidate.objects.filter(pk=linked.pk).exists())
        self.assertContains(deleted,'Не удалено связанных карточек: 1')
        self.assertEqual(self.client.post(bulk_url,{'action':'archive','selected':[foreign.pk]}).status_code,403)
        self.assertTrue(TalentPoolCandidate.objects.filter(pk=foreign.pk).exists())

    def test_ready_for_arrival_tab_contains_only_transferred_candidates(self):
        from .models import TalentPoolCandidate
        ready=TalentPoolCandidate.objects.create(company=self.company,full_name='Поставленный на заезд',stage='TRANSFERRED',
            position=self.position,construction_object=self.obj,camp=self.camp,created_by=self.recruiter)
        waiting=TalentPoolCandidate.objects.create(company=self.company,full_name='Готов, но ещё не поставлен',stage='READY',
            position=self.position,construction_object=self.obj,ready_for_arrival=True,ready_for_arrival_on=self.day,
            created_by=self.recruiter)
        other_project=Project.objects.create(company=self.company,name='Другой проект готовности')
        other_object=ConstructionObject.objects.create(company=self.company,project=other_project,name='Другой объект готовности')
        other_position=Position.objects.create(company=self.company,name='Другая должность готовности')
        other_ready=TalentPoolCandidate.objects.create(company=self.company,full_name='Другой поставленный сотрудник',stage='TRANSFERRED',
            position=other_position,construction_object=other_object,camp=self.unlinked,created_by=self.recruiter)
        self.client.force_login(self.recruiter)
        page=self.client.get(reverse('personnel:recruitment_ready'),{
            'project':[self.obj.pk],'camp':[self.camp.pk],'q':'Поставленный','position':[self.position.pk]})
        self.assertEqual(page.status_code,200);self.assertEqual(page.context['recruitment_page'],'ready')
        self.assertEqual(page.context['candidate_total'],1)
        self.assertContains(page,'Сотрудники, переведённые на этап «Поставлен на заезд»')
        self.assertContains(page,ready.full_name);self.assertNotContains(page,waiting.full_name);self.assertNotContains(page,other_ready.full_name)
        self.assertContains(page,'Показаны только сотрудники, которые уже поставлены на заезд')
        self.assertContains(page,'data-candidate-inline-target',html=False)
        self.assertNotContains(page,'candidate_stage=',html=False)
        self.assertContains(page,'aria-label="Фильтры готовых к заезду"',html=False)
        self.assertNotContains(page,'id="request-state"',html=False)
        content=page.content.decode()
        self.assertLess(content.index('>Проект</label>'),content.index('>Местонахождение</label>'))
        self.assertLess(content.index('>Местонахождение</label>'),content.index('>Сотрудник</label>'))
        self.assertLess(content.index('>Сотрудник</label>'),content.index('>Должность</label>'))
        self.assertNotContains(page,'Оформить заявку на заезд')
        self.client.force_login(self.dispatch)
        dispatcher_page=self.client.get(reverse('personnel:recruitment_ready'),{
            'project':[self.obj.pk],'camp':[self.camp.pk],'q':'Поставленный','position':[self.position.pk]})
        self.assertContains(dispatcher_page,'Оформить заявку на заезд')
        self.assertContains(dispatcher_page,'Оформить выбранных одной заявкой')
        response=self.client.post(reverse('personnel:recruitment_ready_arrival_bulk'),{
            'selected':[ready.pk,other_ready.pk]})
        arrivals=list(MovementReviewRequest.objects.filter(pool_candidate_id__in=[ready.pk,other_ready.pk]).order_by('pk'))
        self.assertEqual(len(arrivals),2);self.assertEqual(len({row.approval_batch for row in arrivals}),1)
        self.assertTrue(response.url.startswith(reverse('personnel:recruitment_arrival_bulk_edit',args=[arrivals[0].approval_batch])))
        refreshed=self.client.get(reverse('personnel:recruitment_ready'),{
            'project':[self.obj.pk],'camp':[self.camp.pk],'q':'Поставленный','position':[self.position.pk]})
        self.assertContains(refreshed,'Заявка на заезд оформлена');self.assertContains(refreshed,'Открыть заявку')
        self.assertNotContains(refreshed,'Оформить заявку на заезд')
        duplicate=self.client.post(reverse('personnel:recruitment_ready_arrival_bulk'),{'selected':[ready.pk]})
        self.assertRedirects(duplicate,reverse('personnel:recruitment_ready'),fetch_redirect_response=False)
        self.assertEqual(MovementReviewRequest.objects.filter(pool_candidate_id__in=[ready.pk,other_ready.pk]).count(),2)
        service.cancel_bulk_arrival_draft(self.dispatch,arrivals[0].approval_batch,'Изменился план')
        ready.refresh_from_db();other_ready.refresh_from_db()
        self.assertEqual(ready.stage,'READY');self.assertEqual(other_ready.stage,'READY')
        self.client.force_login(self.recruiter)
        self.assertEqual(self.client.post(reverse('personnel:recruitment_ready_arrival_bulk'),{'selected':[ready.pk]}).status_code,403)

    def test_perevakhta_can_prepare_ready_candidate_without_dispatcher(self):
        from .models import TalentPoolCandidate
        ready=TalentPoolCandidate.objects.create(company=self.company,full_name='Готовый для перевахты',stage='TRANSFERRED',
            position=self.position,construction_object=self.obj,camp=self.camp,base_city=self.base_city,phone='89990007766',created_by=self.recruiter)
        self.client.force_login(self.perevakhta)
        page=self.client.get(reverse('personnel:recruitment_ready'))
        self.assertEqual(page.status_code,200);self.assertContains(page,ready.full_name)
        self.assertContains(page,'Оформить заявку на заезд');self.assertContains(page,'Оформить выбранных одной заявкой')
        response=self.client.post(reverse('personnel:recruitment_ready_arrival_bulk'),{'selected':[ready.pk]})
        arrival=MovementReviewRequest.objects.get(pool_candidate=ready)
        edit_url=reverse('personnel:recruitment_arrival_bulk_edit',args=[arrival.approval_batch])
        self.assertTrue(response.url.startswith(edit_url));self.assertEqual(arrival.requested_by,self.perevakhta)
        self.assertContains(self.client.get(edit_url),'Общие данные для всех сотрудников')
        self.assertContains(self.client.get(reverse('personnel:my_movement_requests')),'Готовый для перевахты')
        self.assertEqual(self.client.get(reverse('personnel:recruitment_create')).status_code,403)

    def test_shop_compares_position_plan_with_every_funnel_tab_and_exact_request(self):
        from .models import TalentPoolCandidate
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None)
        driver=Position.objects.create(company=self.company,name='Водитель',code='driver-summary')
        unattached=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат без заявки',position=driver,
            stage='REJECTED',is_active=False,created_by=self.recruiter,
            source_data={'funnel_sources':[{'sheet':'План найма'}]})
        self.client.force_login(self.recruiter)
        shop=self.client.get(reverse('personnel:recruitment_list'))
        summary={row['position']:row for row in shop.context['position_summary']}
        montage=summary['Монтажник'];self.assertEqual((montage['request_count'],montage['required'],montage['candidate_count']),(1,2,1))
        self.assertEqual(montage['counts']['work'],1);self.assertEqual(montage['counts']['rejected'],0)
        self.assertEqual([(person['pk'],person['full_name'],person['request_id']) for person in montage['visible_people']],
            [(candidate.pool_candidate_id,candidate.full_name,candidate.request_id)])
        self.assertEqual(summary['Водитель']['counts']['rejected'],1)
        groups={row['value']:row for row in shop.context['funnel_groups']}
        self.assertEqual((shop.context['candidate_position_count'],groups['work']['position_count'],groups['rejected']['position_count']),(2,1,1))
        self.assertContains(shop,'План и воронка по должностям')
        self.assertContains(shop,'data-position-details-action="expand"',html=False);self.assertContains(shop,'data-position-details-action="collapse"',html=False)
        self.assertContains(shop,'data-position-toggle',html=False);self.assertContains(shop,'aria-expanded="true"',html=False)
        self.assertContains(shop,'data-candidate-inline-target',html=False)
        self.assertContains(shop,'data-candidate-inline-row',html=False)
        self.assertNotContains(shop,'data-candidate-inline-toggle',html=False)
        self.assertContains(shop,reverse('personnel:recruitment_candidate_inline',args=[candidate.pool_candidate_id]))
        self.assertContains(shop,f'№{candidate.request_id} · Монтажник · {self.obj}')
        self.assertContains(shop,'Не прикреплён к заявке');self.assertNotContains(shop,'План найма: 1')
        pool=self.client.get(reverse('personnel:recruitment_pool'))
        self.assertContains(pool,f'№{candidate.request_id} · Монтажник · {self.obj}');self.assertContains(pool,'Не прикреплён')
        self.assertContains(pool,unattached.full_name)
        rejected=self.client.get(reverse('personnel:recruitment_list'),{'candidate_stage':'rejected'})
        rejected_summary={row['position']:row for row in rejected.context['position_summary']}
        self.assertNotIn('Монтажник',rejected_summary)
        self.assertEqual([person['full_name'] for person in rejected_summary['Водитель']['visible_people']],[unattached.full_name])
        self.assertContains(rejected,'data-position-toggle',html=False);self.assertContains(rejected,unattached.full_name)

    def test_shop_filters_candidates_by_multiple_positions_camps_and_projects(self):
        first=self.candidate(status='SCREENING',ready_on=None);first.pool_candidate.camp=self.camp
        first.pool_candidate.save(update_fields=['camp','updated_at'])
        driver=Position.objects.create(company=self.company,name='Водитель фильтра',code='driver-filter')
        second_object=ConstructionObject.objects.create(company=self.company,project=self.project,name='Второй объект фильтра')
        second_camp=Camp.objects.create(company=self.company,name='Второе местонахождение фильтра')
        second_request=service.save_request(self.dispatch,self.values(construction_object=second_object,
            position_ref=driver,position=driver.name,quantity=1))
        second=self.candidate(second_request,status='SCREENING',ready_on=None);second.pool_candidate.camp=second_camp
        second.pool_candidate.save(update_fields=['camp','updated_at'])
        self.client.force_login(self.recruiter);url=reverse('personnel:recruitment_list')
        response=self.client.get(url,{'position':[self.position.pk,driver.pk],'camp':[self.camp.pk,second_camp.pk],
            'project':[self.obj.pk,second_object.pk]})
        self.assertEqual(response.context['candidate_total'],2)
        self.assertEqual(response.context['selected_position_ids'],sorted([self.position.pk,driver.pk]))
        self.assertContains(response,'name="position" multiple',html=False)
        self.assertContains(response,'name="camp" multiple',html=False)
        self.assertContains(response,'name="project" multiple',html=False)
        self.assertContains(response,'Проект → КТП');self.assertContains(response,'Второй объект фильтра')
        self.assertContains(response,f'position={self.position.pk}&amp;position={driver.pk}',html=False)
        only_first=self.client.get(url,{'position':[self.position.pk],'camp':[self.camp.pk],'project':[self.obj.pk]})
        self.assertEqual(only_first.context['candidate_total'],1)
        self.assertContains(only_first,first.full_name);self.assertNotContains(only_first,second.full_name)
        only_second=self.client.get(url,{'position':[driver.pk],'camp':[second_camp.pk],'project':[second_object.pk]})
        self.assertEqual(only_second.context['candidate_total'],1)
        self.assertContains(only_second,second.full_name);self.assertNotContains(only_second,first.full_name)

    def test_full_candidate_card_can_be_loaded_and_saved_inline(self):
        from .models import TalentPoolStageEvent
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None);pool=candidate.pool_candidate
        pool.source_data={**(pool.source_data or {}),'agency':'Историческое агентство'}
        pool.save(update_fields=['source_data','updated_at'])
        self.client.force_login(self.recruiter)
        url=reverse('personnel:recruitment_candidate_inline',args=[pool.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'data-candidate-inline-form',html=False)
        self.assertContains(response,'name="passport"',html=False)
        self.assertContains(response,'name="position"',html=False)
        self.assertContains(response,'name="construction_object"',html=False)
        self.assertContains(response,'name="responsible_hr"',html=False)
        self.assertNotContains(response,'name="agency"',html=False)
        self.assertNotContains(response,'Название агентства')
        self.assertContains(response,'candidate-inline-compact-grid',html=False)
        self.assertContains(response,'candidate-inline-form-row-primary',html=False)
        self.assertContains(response,'candidate-inline-form-row-contact',html=False)
        self.assertContains(response,'candidate-inline-form-row-readiness',html=False)
        self.assertContains(response,'data-save-state="clean"',html=False)
        self.assertContains(response,'data-candidate-save disabled',html=False)
        self.assertNotContains(response,'Свернуть')
        self.assertContains(response,'>Сохранить<',html=False)
        invalid=self.client.post(url,{'full_name':'','stage':'SCREENING','phone':'','notes':''})
        self.assertEqual(invalid.status_code,422)
        self.assertContains(invalid,'data-save-state="error"',html=False,status_code=422)
        self.assertContains(invalid,'Исправьте ошибки и сохраните.',status_code=422)
        response=self.client.post(url,{
            'full_name':'Изменённый кандидат','stage':'SCREENING','stage_reason':'Повторная проверка',
            'position':self.position.pk,'construction_object':self.obj.pk,'camp':self.camp.pk,
            'phone':'89990000000','birth_date':'1990-02-03','passport':'1234 567890',
            'base_city':self.base_city.pk,'new_base_city':'','ready_for_arrival_on':'',
            'manager_approved_on':'','security_approved_on':'','medical_exam_on':'','skzi_until':'',
            'pass_documents':'','responsible_hr':'','agency':'','notes':'Изменено на главной'})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Сохранено.')
        pool.refresh_from_db()
        self.assertEqual((pool.full_name,pool.stage,pool.phone, pool.notes),
            ('Изменённый кандидат','SCREENING','89990000000','Изменено на главной'))
        self.assertEqual(pool.source_data['agency'],'Историческое агентство')
        self.assertEqual(pool.position,self.position);self.assertEqual(pool.construction_object,self.obj)
        event=TalentPoolStageEvent.objects.get(candidate=pool)
        self.assertEqual((event.from_stage,event.to_stage,event.note),
            ('MANAGER_APPROVAL','SCREENING','Повторная проверка'))

    def test_inline_candidate_card_is_read_only_outside_recruitment_role_and_company_scoped(self):
        from .models import TalentPoolCandidate
        candidate=self.candidate(status='NEW',ready_on=None);pool=candidate.pool_candidate
        url=reverse('personnel:recruitment_candidate_inline',args=[pool.pk])
        self.client.force_login(self.dispatch)
        response=self.client.get(url)
        self.assertEqual(response.status_code,200);self.assertNotContains(response,'Сохранить карточку')
        self.assertContains(response,'disabled',html=False)
        self.assertEqual(self.client.post(url,{'full_name':'Нельзя изменить'}).status_code,403)
        foreign_pool=TalentPoolCandidate.objects.create(company=self.other,full_name='Чужой кандидат',
            position=Position.objects.create(company=self.other,name='Чужая должность',code='foreign-inline'),
            construction_object=ConstructionObject.objects.create(company=self.other,project=Project.objects.create(
                company=self.other,name='Чужой проект'),name='Чужой объект'),created_by=self.foreign)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_candidate_inline',args=[foreign_pool.pk])).status_code,404)

    def test_recruiter_changes_pool_stage_inline_in_both_directions(self):
        from .models import TalentPoolStageEvent
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None);pool=candidate.pool_candidate
        self.client.force_login(self.recruiter)
        url=reverse('personnel:recruitment_candidate_stage',args=[pool.pk])
        response=self.client.post(url,{'stage':'REJECTED','note':'Не прошёл проверку'})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['stage'],'REJECTED')
        pool.refresh_from_db();self.assertEqual(pool.stage,'REJECTED');self.assertFalse(pool.is_active)
        event=TalentPoolStageEvent.objects.get(candidate=pool)
        self.assertEqual((event.from_stage,event.to_stage,event.note),('MANAGER_APPROVAL','REJECTED','Не прошёл проверку'))
        response=self.client.post(url,{'stage':'SCREENING','note':'Повторное рассмотрение'})
        self.assertEqual(response.status_code,200)
        pool.refresh_from_db();self.assertEqual(pool.stage,'SCREENING');self.assertTrue(pool.is_active)
        self.assertEqual(TalentPoolStageEvent.objects.filter(candidate=pool).count(),2)

    def test_inline_pool_stage_rejects_unknown_stage(self):
        candidate=self.candidate(status='NEW',ready_on=None);pool=candidate.pool_candidate
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_candidate_stage',args=[pool.pk]),{'stage':'UNKNOWN'})
        self.assertEqual(response.status_code,400)
        pool.refresh_from_db();self.assertEqual(pool.stage,'NEW')

    def test_approval_route_advances_in_order_and_records_completed_checks(self):
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None);pool=candidate.pool_candidate
        self.client.force_login(self.recruiter)
        url=reverse('personnel:recruitment_candidate_stage_advance',args=[pool.pk])
        expected=[
            ('SECURITY_CHECK','manager_approved'),
            ('MEDICAL_DOCUMENTS','security_approved'),
            ('PASS_PROCESSING','medical_exam_passed'),
        ]
        for stage,flag in expected:
            response=self.client.post(url)
            self.assertRedirects(response,reverse('personnel:recruitment_list'),fetch_redirect_response=False)
            pool.refresh_from_db();self.assertEqual(pool.stage,stage);self.assertTrue(getattr(pool,flag))
        self.assertEqual(TalentPoolStageEvent.objects.filter(candidate=pool).count(),3)
        inline=reverse('personnel:recruitment_candidate_inline',args=[pool.pk])
        response=self.client.post(inline,{
            'full_name':pool.full_name,'stage':'PASS_PROCESSING','position':self.position.pk,
            'construction_object':self.obj.pk,'phone':pool.phone,'base_city':'','new_base_city':'',
            'ready_for_arrival_on':'','manager_approved':'1','manager_approved_on':pool.manager_approved_on.isoformat(),
            'security_approved':'1','security_approved_on':pool.security_approved_on.isoformat(),
            'medical_exam_passed':'1','medical_exam_on':pool.medical_exam_on.isoformat(),
            'pass_documents_sent':'1','pass_documents_sent_on':'2026-11-22',
            'pass_ready':'1','pass_ready_on':'2026-11-23','responsible_hr':'','notes':''})
        self.assertEqual(response.status_code,200)
        pool.refresh_from_db();self.assertEqual(pool.stage,'READY');self.assertTrue(pool.ready_for_arrival)
        self.assertEqual(pool.ready_for_arrival_on,date(2026,11,23));self.assertTrue(pool.pass_documents_sent);self.assertTrue(pool.pass_ready)
        self.assertTrue(TalentPoolStageEvent.objects.filter(candidate=pool,from_stage='PASS_PROCESSING',to_stage='READY').exists())

    def test_inline_save_returns_managed_stage_to_first_unconfirmed_step(self):
        from .models import TalentPoolStageEvent
        candidate=self.candidate(status='MEDICAL_DOCUMENTS',ready_on=None);pool=candidate.pool_candidate
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_candidate_inline',args=[pool.pk]),{
            'full_name':pool.full_name,'stage':'MEDICAL_DOCUMENTS','position':self.position.pk,
            'construction_object':self.obj.pk,'phone':pool.phone,'base_city':'','new_base_city':'',
            'ready_for_arrival_on':'','manager_approved_on':'','security_approved_on':'',
            'medical_exam_on':'','pass_documents_sent_on':'','pass_ready_on':'',
            'responsible_hr':'','notes':''})
        self.assertEqual(response.status_code,200)
        pool.refresh_from_db();self.assertEqual(pool.stage,'MANAGER_APPROVAL')
        self.assertContains(response,'Направлен на согласование Заказчику')
        event=TalentPoolStageEvent.objects.get(candidate=pool)
        self.assertEqual((event.from_stage,event.to_stage),('MEDICAL_DOCUMENTS','MANAGER_APPROVAL'))

    def test_approval_stage_advance_supports_inline_ajax_without_redirect(self):
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None);pool=candidate.pool_candidate
        self.client.force_login(self.recruiter)
        inline=self.client.get(reverse('personnel:recruitment_candidate_inline',args=[pool.pk]))
        self.assertContains(inline,'data-candidate-stage-advance',html=False)
        response=self.client.post(reverse('personnel:recruitment_candidate_stage_advance',args=[pool.pk]),
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['stage'],'SECURITY_CHECK')
        self.assertNotIn('Location',response)
        pool.refresh_from_db();self.assertTrue(pool.manager_approved)

    def test_approval_route_prevents_skipping_and_marks_overdue_stage(self):
        from .recruitment_stage_flow import stage_status
        candidate=self.candidate(status='MANAGER_APPROVAL',ready_on=None);pool=candidate.pool_candidate
        pool.stage_started_at=timezone.now()-timedelta(days=5);pool.save(update_fields=['stage_started_at','updated_at'])
        status=stage_status(pool)
        self.assertTrue(status['overdue']);self.assertEqual(status['overdue_days'],2)
        self.client.force_login(self.recruiter)
        skipped=self.client.post(reverse('personnel:recruitment_candidate_stage',args=[pool.pk]),{'stage':'MEDICAL_DOCUMENTS'})
        self.assertEqual(skipped.status_code,400)
        inline=self.client.get(reverse('personnel:recruitment_candidate_inline',args=[pool.pk]))
        self.assertContains(inline,'Этап 1 из 5');self.assertContains(inline,'Просрочено на 2 дн.')
        self.assertContains(inline,'Этап выполнен → Направлен на согласование в СЭБ')

    def test_medical_stage_deadline_counts_working_days(self):
        from .recruitment_stage_flow import stage_status
        candidate=self.candidate(status='MEDICAL_DOCUMENTS',ready_on=None);pool=candidate.pool_candidate
        pool.stage_started_at=timezone.make_aware(datetime(2026,11,23,9,0));pool.save(update_fields=['stage_started_at','updated_at'])
        status=stage_status(pool,today=date(2026,11,23))
        self.assertEqual(status['deadline'].isoformat(),'2026-12-07')
        self.assertEqual((status['limit'],status['unit']),(10,'рабочих дней'))

    def test_ready_pass_requires_sent_documents_and_date(self):
        candidate=self.candidate(status='PASS_PROCESSING',ready_on=None);pool=candidate.pool_candidate
        pool.manager_approved=True;pool.manager_approved_on=date(2026,11,18)
        pool.security_approved=True;pool.security_approved_on=date(2026,11,19)
        pool.medical_exam_passed=True;pool.medical_exam_on=date(2026,11,20)
        pool.save(update_fields=['manager_approved','manager_approved_on','security_approved','security_approved_on',
            'medical_exam_passed','medical_exam_on','updated_at'])
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_candidate_inline',args=[pool.pk]),{
            'full_name':pool.full_name,'stage':'PASS_PROCESSING','position':self.position.pk,
            'construction_object':self.obj.pk,'phone':pool.phone,'base_city':'','new_base_city':'',
            'ready_for_arrival_on':'','manager_approved':'1','manager_approved_on':'2026-11-18',
            'security_approved':'1','security_approved_on':'2026-11-19',
            'medical_exam_passed':'1','medical_exam_on':'2026-11-20',
            'pass_ready':'1','pass_ready_on':'2026-11-23','responsible_hr':'','notes':''})
        self.assertEqual(response.status_code,422)
        self.assertContains(response,'Сначала отметьте, что документы направлены на пропуск.',status_code=422)
        pool.refresh_from_db();self.assertEqual(pool.stage,'PASS_PROCESSING');self.assertFalse(pool.pass_ready)

    def test_manual_pool_card_saves_store_fields(self):
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        specialist=RecruitmentSpecialist.objects.create(company=self.company,name='Петрова',user=self.recruiter)
        position=Position.objects.create(company=self.company,name='Электромонтажник',code='elektromontazhnik')
        self.client.force_login(self.recruiter)
        form_page=self.client.get(reverse('personnel:recruitment_pool_create'))
        self.assertContains(form_page,'<select name="responsible_hr"',html=False)
        self.assertContains(form_page,'Петрова')
        response=self.client.post(reverse('personnel:recruitment_pool_create'),{
            'full_name':'Ручной Кандидат','position':position.pk,'construction_object':self.obj.pk,'camp':self.camp.pk,'phone':'89990001122',
            'birth_date':'1990-02-03','passport':'1234 567890','base_city':self.base_city.pk,'new_base_city':'',
            'ready_for_arrival':'1','ready_for_arrival_on':'2026-11-20','manager_approved':'1','manager_approved_on':'2026-11-18',
            'security_approved':'1','security_approved_on':'2026-11-19','medical_exam_passed':'1','medical_exam_on':'2026-11-19',
            'skzi_until':'2027-01-01','pass_documents_sent':'1','pass_documents_sent_on':'2026-11-20',
            'pass_ready':'1','pass_ready_on':'2026-11-21',
            'responsible_hr':specialist.pk,'agency':'Агентство','stage':'NEW','notes':'Комментарий'})
        self.assertRedirects(response,reverse('personnel:recruitment_pool'),fetch_redirect_response=False)
        row=TalentPoolCandidate.objects.get(full_name='Ручной Кандидат')
        self.assertEqual(row.position,position);self.assertEqual(row.construction_object,self.obj);self.assertEqual(row.camp,self.camp)
        self.assertEqual(row.source_data['position'],'Электромонтажник');self.assertEqual(row.source_data['construction_site'],'КТП')
        self.assertEqual(row.source_data['birth_date'],'1990-02-03');self.assertEqual(row.source_data['base_city'],'Уфа')
        self.assertEqual(row.responsible_hr,specialist);self.assertEqual(row.source_data['responsible_hr'],'Петрова')
        self.assertTrue(row.ready_for_arrival);self.assertEqual(row.ready_for_arrival_on.isoformat(),'2026-11-21')
        self.assertTrue(row.manager_approved);self.assertTrue(row.security_approved);self.assertTrue(row.medical_exam_passed)
        self.assertTrue(row.pass_documents_sent);self.assertTrue(row.pass_ready);self.assertEqual(row.stage,'READY')
        page=self.client.get(reverse('personnel:recruitment_pool'))
        self.assertContains(page,'Электромонтажник');self.assertContains(page,'Все данные кандидата')
        duplicate=self.client.post(reverse('personnel:recruitment_pool_create'),{
            'full_name':'  ручной   кандидат ','phone':'8 (999) 000-11-22','stage':'NEW','notes':'Повтор'})
        self.assertEqual(duplicate.status_code,200);self.assertContains(duplicate,'Такой кандидат уже есть в общем пуле')
        self.assertEqual(TalentPoolCandidate.objects.filter(company=self.company).count(),1)

    def test_recruitment_specialist_directory_navigation_crud_and_scope(self):
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        existing=RecruitmentSpecialist.objects.create(company=self.company,name='Дарина Н.',user=self.recruiter)
        foreign=RecruitmentSpecialist.objects.create(company=self.other,name='Чужой рекрутер',user=self.foreign)
        candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Кандидат рекрутера',position=self.position,
            construction_object=self.obj,responsible_hr=existing,created_by=self.recruiter,source_data={'responsible_hr':'Дарина Н.'})
        url=reverse('personnel:recruitment_specialists')
        self.client.force_login(self.hr_head)
        bank=self.client.get(reverse('personnel:recruitment_list'))
        sidebar=bank.content.decode().split('<aside class="sidebar"',1)[1].split('</aside>',1)[0]
        self.assertEqual(sidebar.count(f'href="{url}"'),2)
        page=self.client.get(url)
        self.assertContains(page,'Справочник рекрутеров');self.assertContains(page,'Дарина Н.')
        self.assertContains(page,self.recruiter.username);self.assertNotContains(page,foreign.name)
        self.assertContains(page,reverse('personnel:recruitment_specialist_delete',args=[existing.pk]));self.assertContains(page,'Удалить')
        create=reverse('personnel:recruitment_specialist_create')
        response=self.client.post(create,{'name':'Новый рекрутер','user':self.recruiter2.pk,'is_active':'on'})
        self.assertRedirects(response,url)
        saved=RecruitmentSpecialist.objects.get(company=self.company,name='Новый рекрутер')
        self.assertEqual(saved.user,self.recruiter2);self.assertTrue(saved.is_active)
        duplicate=self.client.post(create,{'name':'  новый   РЕКРУТЕР ','user':'','is_active':'on'})
        self.assertEqual(duplicate.status_code,200);self.assertContains(duplicate,'Такой рекрутер уже есть в справочнике.')
        invalid=self.client.post(create,{'name':'2026-02-12','user':'','is_active':'on'})
        self.assertEqual(invalid.status_code,200);self.assertContains(invalid,'Укажите корректное ФИО или краткое имя HR.')
        edit=reverse('personnel:recruitment_specialist_edit',args=[existing.pk])
        self.assertRedirects(self.client.post(edit,{'name':'Дарина Николаевна','user':self.recruiter.pk}),url)
        existing.refresh_from_db();self.assertEqual(existing.name,'Дарина Николаевна');self.assertFalse(existing.is_active)
        self.assertEqual(self.client.get(reverse('personnel:recruitment_specialist_edit',args=[foreign.pk])).status_code,404)
        delete=reverse('personnel:recruitment_specialist_delete',args=[existing.pk])
        self.assertEqual(self.client.get(delete).status_code,405)
        self.assertRedirects(self.client.post(delete),url)
        existing.refresh_from_db();self.assertFalse(existing.is_active)
        candidate.refresh_from_db();self.assertEqual(candidate.responsible_hr_id,existing.pk);self.assertEqual(candidate.source_data['responsible_hr'],'Дарина Н.')
        self.assertTrue(User.objects.filter(pk=self.recruiter.pk).exists())
        self.assertEqual(self.client.post(reverse('personnel:recruitment_specialist_delete',args=[foreign.pk])).status_code,404)
        self.client.force_login(self.recruiter)
        readonly=self.client.get(url)
        self.assertEqual(readonly.status_code,200);self.assertNotContains(readonly,'Добавить рекрутера');self.assertNotContains(readonly,'Удалить')
        self.assertEqual(self.client.post(create,{'name':'Нельзя добавить','is_active':'on'}).status_code,403)
        self.assertEqual(self.client.post(reverse('personnel:recruitment_specialist_delete',args=[saved.pk])).status_code,403)

    def test_recruitment_specialists_support_company_scoped_bulk_selection_and_delete(self):
        from .models import RecruitmentSpecialist,TalentPoolCandidate
        first=RecruitmentSpecialist.objects.create(company=self.company,name='Первый рекрутер',user=self.recruiter)
        second=RecruitmentSpecialist.objects.create(company=self.company,name='Второй рекрутер',user=self.recruiter2)
        foreign=RecruitmentSpecialist.objects.create(company=self.other,name='Чужой рекрутер',user=self.foreign)
        first_candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Первый кандидат',
            responsible_hr=first,created_by=self.recruiter)
        second_candidate=TalentPoolCandidate.objects.create(company=self.company,full_name='Второй кандидат',
            responsible_hr=second,created_by=self.recruiter)
        url=reverse('personnel:recruitment_specialists')
        bulk_url=reverse('personnel:recruitment_specialist_bulk_delete')
        self.client.force_login(self.hr_head)
        page=self.client.get(url)
        self.assertContains(page,'select-all-specialists',html=False)
        self.assertContains(page,'class="form-check-input specialist-selection"',count=2,html=False)
        self.assertContains(page,'Удалить неиспользуемых')
        self.assertEqual(self.client.get(bulk_url).status_code,405)
        self.assertEqual(self.client.post(bulk_url,{'selected':[first.pk,foreign.pk]}).status_code,403)
        self.assertTrue(RecruitmentSpecialist.objects.filter(pk=first.pk).exists())
        response=self.client.post(bulk_url,{'selected':[first.pk,second.pk]})
        self.assertRedirects(response,url)
        self.assertEqual(RecruitmentSpecialist.objects.filter(pk__in=[first.pk,second.pk],is_active=False).count(),2)
        first_candidate.refresh_from_db();second_candidate.refresh_from_db()
        self.assertEqual(first_candidate.responsible_hr_id,first.pk);self.assertEqual(second_candidate.responsible_hr_id,second.pk)
        self.assertEqual(User.objects.filter(pk__in=[self.recruiter.pk,self.recruiter2.pk]).count(),2)
        self.client.force_login(self.recruiter)
        self.assertEqual(self.client.post(bulk_url,{'selected':[foreign.pk]}).status_code,403)

    def test_pool_confirmation_flags_require_dates_and_can_add_base_city(self):
        from .models import TalentPoolCandidate
        pool=TalentPoolCandidate.objects.create(company=self.company,full_name='Проверка дат',stage='SCREENING',
            created_by=self.recruiter,position=self.position,construction_object=self.obj)
        self.client.force_login(self.recruiter);url=reverse('personnel:recruitment_pool_edit',args=[pool.pk])
        response=self.client.post(url,{'full_name':pool.full_name,'stage':'SCREENING','position':self.position.pk,
            'construction_object':self.obj.pk,'new_base_city':'Норильск','ready_for_arrival':'1','phone':'','notes':''})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Укажите дату подтверждения.')
        response=self.client.post(url,{'full_name':pool.full_name,'stage':'SCREENING','position':self.position.pk,
            'construction_object':self.obj.pk,'new_base_city':'Норильск','ready_for_arrival':'1',
            'ready_for_arrival_on':'2026-11-20','phone':'','notes':''})
        self.assertRedirects(response,reverse('personnel:recruitment_pool'),fetch_redirect_response=False)
        pool.refresh_from_db();self.assertEqual(pool.base_city.name,'Норильск');self.assertTrue(pool.ready_for_arrival)

    def test_dudinka_arrival_is_read_from_ticket_then_actual_movement(self):
        candidate=self.candidate(status='READY',ready_on=self.day);prep=service.handoff(self.recruiter,candidate.pk,candidate.revision)
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),workflow=True)
        movement=prep.movement_request;movement.tickets_ready=True;movement.movement_date=self.day
        movement.save(update_fields=['tickets_ready','movement_date','updated_at'])
        pool=type(candidate.pool_candidate).objects.get(pk=candidate.pool_candidate_id)
        self.assertEqual(pool.dudinka_arrival,{'date':self.day,'actual':False})
        movement.hub_arrived_on=self.day+timedelta(days=1);movement.save(update_fields=['hub_arrived_on','updated_at'])
        pool=type(pool).objects.get(pk=pool.pk)
        self.assertEqual(pool.dudinka_arrival,{'date':self.day+timedelta(days=1),'actual':True})
        self.client.force_login(self.recruiter)
        card=self.client.get(reverse('personnel:recruitment_candidate_card',args=[pool.pk]))
        self.assertContains(card,'факт')
    def test_rejected_candidate_can_return_to_any_funnel_stage(self):
        from .models import TalentPoolCandidate,TalentPoolStageEvent
        from .recruitment_forms import CandidateForm
        pool=TalentPoolCandidate.objects.create(company=self.company,full_name='Вернувшийся Кандидат',phone='89995554433',
            stage='REJECTED',is_active=False,created_by=self.recruiter)
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_pool_edit',args=[pool.pk]),{
            'full_name':pool.full_name,'phone':pool.phone,'stage':'SCREENING','stage_reason':'Рассматриваем повторно',
            'position':self.position.pk,'construction_object':self.obj.pk,'notes':''})
        self.assertRedirects(response,reverse('personnel:recruitment_pool'),fetch_redirect_response=False)
        pool.refresh_from_db();self.assertEqual(pool.stage,'SCREENING');self.assertTrue(pool.is_active)
        event=TalentPoolStageEvent.objects.get(candidate=pool)
        self.assertEqual((event.from_stage,event.to_stage),('REJECTED','SCREENING'));self.assertEqual(event.actor,self.recruiter)
        self.assertEqual(event.note,'Рассматриваем повторно')
        vacancy=self.row();service.assign_request(self.hr_head,vacancy.pk,self.recruiter,vacancy.revision)
        self.assertIn(pool,CandidateForm(company=self.company,request_row=vacancy).fields['pool_candidate'].queryset)
        card=self.client.get(reverse('personnel:recruitment_candidate_card',args=[pool.pk]))
        self.assertContains(card,'Отказ работодателя → На рассмотрении');self.assertContains(card,'Рассматриваем повторно')
    def test_rejected_application_can_be_moved_back_to_review(self):
        candidate=self.candidate(status='REJECTED',ready_on=None)
        pool=candidate.pool_candidate;pool.refresh_from_db();self.assertFalse(pool.is_active)
        updated=service.save_candidate(self.recruiter,candidate.request_id,{
            'pool_candidate':pool,'full_name':candidate.full_name,'phone':candidate.phone,'status':'HR_INTERVIEW',
            'ready_on':None,'notes':'Повторное собеседование'},candidate.pk,candidate.revision)
        pool.refresh_from_db();self.assertEqual(updated.status,'HR_INTERVIEW');self.assertEqual(pool.stage,'HR_INTERVIEW')
        self.assertTrue(pool.is_active)
