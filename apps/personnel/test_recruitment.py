from datetime import timedelta
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
from apps.resources.models import Employee
from .models import (Camp, CampObjectLink, CampStay, Person, RecruitmentRequest,
                     RecruitmentCandidate, ArrivalPreparation, CandidateDocument, HousingEvent)
from . import recruitment as service


class RecruitmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Подбор test')
        cls.other=Company.objects.create(name='Другая компания')
        cls.project=Project.objects.create(company=cls.company,name='Проект')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='КТП')
        cls.camp=Camp.objects.create(company=cls.company,name='ВЖГ')
        CampObjectLink.objects.create(company=cls.company,camp=cls.camp,construction_object=cls.obj)
        cls.unlinked=Camp.objects.create(company=cls.company,name='Другой ВЖГ')
        cls.foreign_camp=Camp.objects.create(company=cls.other,name='Чужой ВЖГ')
        cls.day=timezone.localdate()-timedelta(days=1)
        cls.dispatch=User.objects.create_user(username='dispatch-recruit',company=cls.company,role=Role.objects.get(code='PERSONNEL_DISPATCHER'))
        cls.recruiter=User.objects.create_user(username='recruiter',company=cls.company,role=Role.objects.get(code='RECRUITER'))
        cls.recruiter2=User.objects.create_user(username='recruiter2',company=cls.company,role=Role.objects.get(code='RECRUITER'))
        cls.foreign=User.objects.create_user(username='recruit-foreign',company=cls.other,role=Role.objects.get(code='RECRUITER'))
        cls.employee=Employee.objects.create(company=cls.company,last_name='Оформленный',first_name='Сотрудник',employee_number='R-001')
        cls.foreign_employee=Employee.objects.create(company=cls.other,last_name='Чужой',first_name='Сотрудник')
    def values(self,**extra):
        return {'construction_object':self.obj,'position':'Монтажник','quantity':2,'required_on':self.day,'requirements':'Удостоверение',**extra}
    def row(self):
        return service.save_request(self.dispatch,self.values())
    def candidate(self,row=None,**extra):
        row=row or self.row()
        service.take_request(self.recruiter,row.pk,row.revision)
        return service.save_candidate(self.recruiter,row.pk,{'full_name':'Кандидат Новый','phone':'123','status':'READY','ready_on':self.day,'notes':'Согласован',**extra})
    def preparation(self):
        c=self.candidate();return service.handoff(self.recruiter,c.pk,c.revision)
    def arrival_values(self,**extra):
        return {'expected_on':self.day,'camp':self.camp,'employee':self.employee,'notes':'Заезд','actual_on':self.day,**extra}
    def assertValidation(self,fn,*args,**kwargs):
        with self.assertRaises(ValidationError):fn(*args,**kwargs)

    def test_complete_flow_counts_and_no_automatic_employee_or_fact(self):
        row=self.row();self.assertEqual(service.progress(row),{'required':2,'shortlisted':0,'ready':0,'arrived':0,'label':'Новая'})
        before=Employee.objects.count();c=self.candidate(row)
        self.assertEqual(Employee.objects.count(),before);self.assertFalse(Person.objects.exists());self.assertFalse(CampStay.objects.exists())
        prep=service.handoff(self.recruiter,c.pk,c.revision)
        self.assertEqual(service.progress(RecruitmentRequest.objects.get(pk=row.pk))['ready'],1)
        self.assertFalse(CampStay.objects.exists())
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values())
        self.assertFalse(CampStay.objects.exists());self.assertFalse(Person.objects.exists())
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),confirm=True)
        self.assertTrue(prep.actually_arrived);self.assertEqual(prep.actual_stay.person.employee_id,self.employee.pk)
        self.assertEqual(HousingEvent.objects.get().kind,'ARRIVAL');self.assertEqual(service.progress(RecruitmentRequest.objects.get(pk=row.pk))['arrived'],1)
        self.assertEqual(Employee.objects.count(),before)
    def test_candidate_can_be_sent_from_vacancy_into_arrival_workflow(self):
        from apps.planning.models import WorkTask
        prep=self.preparation()
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),workflow=True)
        self.assertIsNotNone(prep.movement_request_id)
        self.assertEqual(prep.movement_request.status,'PENDING_APPROVAL')
        self.assertEqual(prep.movement_request.employment_kind,'NEW')
        task=WorkTask.objects.get(key=f'personnel-movement-approval:{prep.movement_request_id}',target_role='MANAGER')
        manager=User.objects.create_user(username='recruitment-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        self.client.force_login(manager)
        self.assertRedirects(self.client.get(reverse('planning:task_detail',args=[task.pk])),
            reverse('personnel:movement_review_accept',args=[prep.movement_request_id]),fetch_redirect_response=False)
        self.assertFalse(CampStay.objects.exists())
    def test_new_candidate_with_ready_date_can_be_opened_for_arrival_by_dispatcher(self):
        row=self.row();service.take_request(self.recruiter,row.pk,row.revision)
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
        self.assertContains(recommendations,'Можно оформлять');self.assertContains(recommendations,'Оформить заезд')
        response=self.client.post(reverse('personnel:recruitment_handoff',args=[candidate.pk]),{'revision':candidate.revision})
        prep=ArrivalPreparation.objects.get(candidate=candidate)
        self.assertRedirects(response,reverse('personnel:recruitment_arrival',args=[prep.pk]),fetch_redirect_response=False)
        arrival=self.client.get(reverse('personnel:recruitment_arrival',args=[prep.pk]))
        self.assertContains(arrival,'ФИО кандидата');self.assertContains(arrival,'value="Новый кандидат"')
        self.assertContains(arrival,'Проект');self.assertContains(arrival,f'value="{self.project.name}"')
        self.assertContains(arrival,'Объект / участок');self.assertContains(arrival,f'value="{self.obj.name}"')
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
        row=service.save_request(self.dispatch,self.values(quantity=1));candidate=self.candidate(row)
        prep=service.handoff(self.recruiter,candidate.pk,candidate.revision)
        prep=service.prepare_arrival(self.dispatch,prep.pk,prep.revision,self.arrival_values(),workflow=True)
        movement=prep.movement_request;movement.status='EN_ROUTE';movement.hub_departed_on=self.day
        movement.save(update_fields=['status','hub_departed_on','updated_at'])
        admin=User.objects.create_user(username='arrival-completion-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        arrive_camp(admin,movement,self.day+timedelta(days=1));row.refresh_from_db()
        self.assertIsNotNone(row.completed_at)
        self.client.force_login(self.dispatch)
        active=self.client.get(reverse('personnel:recruitment_list'))
        self.assertNotContains(active,f'№{row.pk} ·')
        completed=self.client.get(reverse('personnel:recruitment_list'),{'state':'completed'})
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
        row=self.row();service.take_request(self.recruiter,row.pk,row.revision)
        pool=TalentPoolCandidate.objects.create(company=self.company,full_name='Человек из пула',phone='555',created_by=self.recruiter,
            source_data={'position':'Монтажник','construction_site':'КТП'})
        form=CandidateForm({'pool_candidate':pool.pk,'full_name':'','phone':'','status':'READY','ready_on':self.day,'notes':'','revision':0},company=self.company,request_row=row)
        self.assertEqual(form.initial['request_position'],row.position)
        self.assertEqual(form.fields['pool_candidate'].label_from_instance(pool),'Человек из пула — Монтажник · КТП')
        self.assertEqual(form.pool_candidate_data[str(pool.pk)],{'full_name':'Человек из пула','phone':'555','notes':''})
        self.assertTrue(form.is_valid(),form.errors)
        candidate=service.save_candidate(self.recruiter,row.pk,form.cleaned_data)
        self.assertEqual(candidate.pool_candidate,pool);self.assertEqual(candidate.full_name,pool.full_name)
        self.assertTrue(pool.vacancy_candidates.filter(request=row).exists())
        self.client.force_login(self.recruiter)
        page=self.client.get(reverse('personnel:recruitment_candidate_create',args=[row.pk]))
        self.assertContains(page,'candidate-pool-data');self.assertContains(page,'candidate.full_name')
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
            service.save_candidate(self.recruiter,row.pk,{'pool_candidate':None,'full_name':'  кандидат   новый ',
                'phone':'(1) 2-3','status':'READY','ready_on':self.day,'notes':''})
        self.assertEqual(row.candidates.count(),1);self.assertEqual(row.candidates.get(),first)
    def test_only_assigned_recruiter_can_work_request(self):
        row=self.row();service.take_request(self.recruiter,row.pk,row.revision)
        row.refresh_from_db();self.assertValidation(service.take_request,self.recruiter2,row.pk,row.revision)
        self.assertValidation(service.save_candidate,self.recruiter2,row.pk,{'full_name':'Чужой','phone':'','status':'NEW','ready_on':None,'notes':''})
        self.assertFalse(RecruitmentCandidate.objects.exists())
    def test_role_separation_in_services(self):
        with self.assertRaises(PermissionDenied):service.save_request(self.recruiter,self.values())
        row=self.row()
        with self.assertRaises(PermissionDenied):service.take_request(self.dispatch,row.pk,row.revision)
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
        with self.assertRaises(RecruitmentRequest.DoesNotExist):service.take_request(self.foreign,row.pk,row.revision)
    def test_private_documents_download_scope_and_upload_validation(self):
        row=self.row();service.take_request(self.recruiter,row.pk,row.revision)
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
        row=self.row();service.take_request(self.recruiter,row.pk,row.revision)
        with TemporaryDirectory() as root,override_settings(ACTUATION_FILES_ROOT=root):
            with patch.object(CandidateDocument,'save',side_effect=ValidationError('Отказ')):
                self.assertValidation(service.save_candidate,self.recruiter,row.pk,{'full_name':'X','phone':'','status':'NEW','ready_on':None,'notes':''},files=[SimpleUploadedFile('a.pdf',b'x')])
            self.assertFalse(RecruitmentCandidate.objects.exists());self.assertFalse([p for p in Path(root).rglob('*') if p.is_file()])
    def test_three_actual_sections_and_request_prefill(self):
        self.client.force_login(self.dispatch)
        for route,title in [('housing','Ведение персонала'),('movements','Новая заявка'),('recruitment_list','Магазин подбора')]:
            page=self.client.get(reverse('personnel:'+route));self.assertContains(page,'data-personnel-sections');self.assertContains(page,title)
        page=self.client.get(reverse('personnel:housing'));self.assertNotContains(page,'data-movement-form')
        page=self.client.get(reverse('personnel:movements'));self.assertContains(page,'data-movement-form');self.assertNotContains(page,'data-roster-grid')
        page=self.client.get(reverse('personnel:recruitment_create'),{'position':'Сварщик','construction_object':self.obj.pk})
        self.assertEqual(page.context['form'].initial['position'],'Сварщик');self.assertEqual(int(page.context['form'].initial['construction_object']),self.obj.pk)
    def test_recruiter_landing_menu_and_direct_route_restrictions(self):
        self.client.force_login(self.recruiter)
        self.assertRedirects(self.client.get(reverse('dashboard:index')),reverse('personnel:recruitment_list'))
        page=self.client.get(reverse('personnel:recruitment_list'));self.assertContains(page,'Магазин подбора');self.assertNotContains(page,'data-paths="/personnel/" hidden')
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
        response=self.client.post(reverse('personnel:recruitment_create'),{'construction_object':self.obj.pk,'position':'Монтажник','quantity':1,'required_on':str(self.day),'requirements':'','revision':0})
        row=RecruitmentRequest.objects.get();self.assertRedirects(response,reverse('personnel:recruitment_detail',args=[row.pk]))
        self.client.force_login(self.recruiter)
        self.client.post(reverse('personnel:recruitment_detail',args=[row.pk]),{'action':'take','revision':row.revision})
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
    def test_new_role_available_to_admin_account_and_access_group_forms(self):
        from apps.accounts.access_groups import GroupForm
        from apps.accounts.forms import CompanyUserAccessForm
        self.assertIn(Role.objects.get(code='RECRUITER'),GroupForm(company=self.company).fields['role'].queryset)
        self.assertIn(Role.objects.get(code='RECRUITER'),CompanyUserAccessForm(company=self.company).fields['role'].queryset)
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
        from .models import TalentPoolCandidate
        from .talent_pool_import import HEADERS,import_sheet
        workbook=Workbook();sheet=workbook.active;sheet.title='Магазин 2026';sheet.append(HEADERS)
        sheet.append([1,'  Иванов   Иван Иванович ','Монтажник','КТП',None,'1234 567890',89991234567,None,None,None,None,None,'Уфа','готов',None,'Петров',None,'Комментарий','Иванов Иван Иванович'])
        payload=BytesIO();workbook.save(payload);payload.seek(0)
        self.assertEqual(import_sheet(self.recruiter,payload),{'created':1,'updated':0,'skipped':0})
        row=TalentPoolCandidate.objects.get();self.assertEqual(row.full_name,'Иванов Иван Иванович');self.assertEqual(row.phone,'89991234567')
        self.assertEqual(row.source_data['position'],'Монтажник');self.assertEqual(row.source_data['base_city'],'Уфа')
        payload.seek(0);self.assertEqual(import_sheet(self.recruiter,payload),{'created':0,'updated':0,'skipped':1})
        self.client.force_login(self.recruiter);page=self.client.get(reverse('personnel:recruitment_pool'))
        self.assertContains(page,'Монтажник');self.assertContains(page,'КТП');self.assertContains(page,'Уфа')
        self.assertContains(page,reverse('personnel:recruitment_pool_import'))
    def test_manual_pool_card_saves_store_fields(self):
        from .models import TalentPoolCandidate
        self.client.force_login(self.recruiter)
        response=self.client.post(reverse('personnel:recruitment_pool_create'),{
            'full_name':'Ручной Кандидат','position':'Электромонтажник','construction_site':'КТП-2','phone':'89990001122',
            'birth_date':'1990-02-03','passport':'1234 567890','base_city':'Уфа','readiness':'Готов с 20 ноября',
            'dudinka_arrival':'2026-11-21','manager_approval':'Согласован','security_approval':'Проверен',
            'skzi_until':'2027-01-01','medical_exam':'Пройден','pass_documents':'Направлены',
            'responsible_hr':'Петрова','agency':'Агентство','notes':'Комментарий','is_active':'on'})
        self.assertRedirects(response,reverse('personnel:recruitment_pool'),fetch_redirect_response=False)
        row=TalentPoolCandidate.objects.get(full_name='Ручной Кандидат')
        self.assertEqual(row.source_data['position'],'Электромонтажник');self.assertEqual(row.source_data['construction_site'],'КТП-2')
        self.assertEqual(row.source_data['birth_date'],'1990-02-03');self.assertEqual(row.source_data['base_city'],'Уфа')
        page=self.client.get(reverse('personnel:recruitment_pool'))
        self.assertContains(page,'Электромонтажник');self.assertContains(page,'Все данные кандидата')
        duplicate=self.client.post(reverse('personnel:recruitment_pool_create'),{
            'full_name':'  ручной   кандидат ','phone':'8 (999) 000-11-22','notes':'Повтор','is_active':'on'})
        self.assertEqual(duplicate.status_code,200);self.assertContains(duplicate,'Такой кандидат уже есть в общем пуле')
        self.assertEqual(TalentPoolCandidate.objects.filter(company=self.company).count(),1)
