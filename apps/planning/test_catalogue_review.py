from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Company, User, Role
from apps.resources.models import Brigade, EquipmentType, CatalogueReviewRequest
from apps.resources import catalogue_review as review
from apps.resources.position_catalog import planning_brigades
from . import test_preparation as fixtures, preparation
from .models import ResourceMonthAllocation, DailyScheduleEntry, WorkResourceAssignment
from .global_services import GlobalPlanService
from .independent_smg import create as create_smg
from .daily_schedule_approval import transition as smg_transition
from .daily_schedule_table import add_entry
from .daily_schedule import save_entry, identities

JAN = date(2026,1,1)


class CatalogueReviewTests(TestCase):
    setUpTestData = classmethod(fixtures.PreparationTests.setUpTestData.__func__)
    handoff = fixtures.PreparationTests.handoff
    checked = fixtures.PreparationTests.checked
    def setUp(self):
        fixtures.PreparationTests.setUp(self)
        self.hr = User.objects.create_user(username='catalogue-review-hr',company=self.company,role=Role.objects.get(code='PERSONNEL_CATALOG_MANAGER'))
        self.tech = User.objects.create_user(username='catalogue-review-tech',company=self.company,role=Role.objects.get(code='EQUIPMENT_CATALOG_MANAGER'))
        self.client.force_login(self.planner)

    def proposal(self,kind='labor',owner=None,name=None,user=None):
        return review.propose(user or self.planner,owner or self.version,kind,name or ('Новая должность' if kind=='labor' else 'Новая техника'),months=[JAN])
    def actor(self,proposal): return self.hr if proposal.kind=='labor' else self.tech
    def confirm(self,proposal): return review.decide(self.actor(proposal),proposal,'confirm',proposal.revision)
    def replace(self,proposal,target):
        token,_=review.preview(self.actor(proposal),proposal,target)
        return review.decide(self.actor(proposal),proposal,'replace',proposal.revision,target=target,token=token)

    def test_planner_and_assigned_manager_propose_and_new_rows_are_editable_in_plan(self):
        self.handoff()
        for kind,actor in [('labor',self.planner),('equipment',self.manager)]:
            p=self.proposal(kind,user=actor)
            self.assertEqual(p.status,'PENDING');self.assertFalse(p.resource.is_active)
            row=self.version.resource_allocations.get(kind=kind)
            self.assertEqual(row.count,0)
        with self.assertRaises(PermissionDenied): self.proposal(name='Чужой руководитель',user=self.other_manager)
        page=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertEqual(page.status_code,200)
        self.assertContains(page,'Отправка на согласование заблокирована')
        self.assertContains(page,'Новая должность');self.assertContains(page,'Новая техника')
        self.assertEqual(len(page.context['catalogue_requests']),2)
        from .workspace_forms import ResourceAllocationForm
        row=self.version.resource_allocations.get(kind='labor')
        form=ResourceAllocationForm({'month':str(JAN),'kind':'labor','brigade':row.brigade_id,'count':'4','hours':'16','liters':'0','balance':'0','rate':'3'},instance=row,user=self.planner,version=self.version,month=JAN)
        self.assertTrue(form.is_valid(),form.errors);form.save();row.refresh_from_db();self.assertEqual(row.count,4)

    def test_both_catalogues_gate_gpr_submission_and_confirmation_allows_it(self):
        self.handoff();self.checked()
        labor=self.proposal();gear=self.proposal('equipment')
        preparation.ready(self.manager,self.version)
        for proposal in [labor,gear]:
            with self.assertRaisesMessage(ValidationError,'Нельзя отправить план на согласование'):
                GlobalPlanService.transition(self.version,self.manager,'submit')
            self.confirm(proposal)
        submitted=GlobalPlanService.transition(self.version,self.manager,'submit')
        self.assertEqual(submitted.status,'SUBMITTED')
        self.assertIn(labor.brigade_id,planning_brigades(self.company).values_list('pk',flat=True))

    def test_reject_requires_reason_retains_quantities_and_removal_clears_only_its_gate(self):
        self.handoff();self.checked();proposal=self.proposal()
        row=self.version.resource_allocations.get();row.count=7;row.hours=56;row.save()
        with self.assertRaises(ValidationError):review.decide(self.hr,proposal,'reject',0)
        review.decide(self.hr,proposal,'reject',0,'Используйте должность из штатного расписания')
        row.refresh_from_db();self.assertEqual((row.count,row.hours),(7,56))
        preparation.ready(self.manager,self.version)
        with self.assertRaisesMessage(ValidationError,'Нельзя отправить'):preparation.require_submission(self.manager,self.version)
        page=self.client.get(reverse('planning:workspace_edit',args=[self.version.pk]))
        self.assertContains(page,'Используйте должность из штатного расписания')
        row.delete();self.assertFalse(review.requests_for(self.version,blocking=True).exists())
        preparation.ready(self.manager,self.version);preparation.require_submission(self.manager,self.version)

    def test_monthly_replacement_merges_counts_hours_and_work_links_without_changing_works(self):
        self.handoff();checked=self.checked();proposal=self.proposal()
        source=self.version.resource_allocations.get();source.count=3;source.hours=24;source.rate=10;source.save()
        target=ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='labor',brigade=self.brigade,count=2,hours=16,rate=20)
        from .resource_links import replace
        replace(source,self.version,[(self.simple.pk,None)])
        old_works=list(self.version.work_allocations.values())
        self.replace(proposal,self.brigade)
        target.refresh_from_db();self.assertEqual((target.count,target.hours,target.rate),(5,40,Decimal('14.00')))
        self.assertEqual(target.graph_links.get().work_id,self.simple.pk)
        self.assertEqual(list(self.version.work_allocations.values()),old_works)
        self.assertFalse(review.requests_for(self.version,blocking=True).exists())
        self.assertIsNone(preparation.record(self.version).resources_ready_at)
        self.assertEqual(checked.status,'APPROVED')
        proposal.refresh_from_db();self.assertEqual(proposal.status,'REPLACED')
        self.assertEqual(proposal.target_brigade,self.brigade)

    def test_preview_rejects_stale_plan_and_catalogue_edits_atomically(self):
        proposal=self.proposal();token,_=review.preview(self.hr,proposal,self.brigade)
        row=self.version.resource_allocations.get();row.count=6;row.save()
        with self.assertRaisesMessage(ValidationError,'План изменился'):
            review.decide(self.hr,proposal,'replace',0,target=self.brigade,token=token)
        proposal.refresh_from_db();self.assertEqual(proposal.status,'PENDING')
        row.refresh_from_db();self.assertEqual(row.brigade_id,proposal.brigade_id)
        token,_=review.preview(self.hr,proposal,self.brigade)
        self.brigade.name='Уточнённая должность';self.brigade.save()
        with self.assertRaises(ValidationError):review.decide(self.hr,proposal,'replace',0,target=self.brigade,token=token)

    def test_scope_role_and_target_checks_prevent_cross_company_or_cross_direction_decisions(self):
        proposal=self.proposal()
        with self.assertRaises(PermissionDenied):review.decide(self.tech,proposal,'confirm',0)
        with self.assertRaises(PermissionDenied):review.preview(self.tech,proposal,self.brigade)
        other=Company.objects.create(name='Foreign catalogue proposals')
        target=Brigade.objects.create(company=other,name='Foreign existing')
        with self.assertRaises(ValidationError):review.preview(self.hr,proposal,target)
        other_user=User.objects.create_user(username='foreign-catalogue-review',company=other,role=self.hr.role)
        with self.assertRaises(PermissionDenied):review.decide(other_user,proposal,'confirm',0)
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(reverse('resources:catalogue_review_detail',args=[proposal.pk])).status_code,404)
        self.client.force_login(self.planner)
        self.assertEqual(self.client.post(reverse('resources:catalogue_review_detail',args=[proposal.pk]),{'action':'confirm','revision':0}).status_code,403)

    def test_duplicate_inactive_pending_and_foreign_creations_are_rejected_without_extra_rows(self):
        self.proposal()
        count=CatalogueReviewRequest.objects.count()
        with self.assertRaises(ValidationError):self.proposal(name=' новая   должность ')
        self.assertEqual(CatalogueReviewRequest.objects.count(),count)
        self.planner.assigned_objects.clear()
        url=reverse('planning:catalogue_propose',args=['gpr',self.version.pk,'labor'])
        self.assertEqual(self.client.post(url,{'name':'Недоступный объект','months':[str(JAN)]}).status_code,404)

    def test_smg_proposal_daily_merge_and_submission_gate(self):
        schedule=create_smg(self.planner,self.obj,JAN)
        add_entry(self.planner,schedule,{'kind':'work','work':self.simple})
        self.handoff(schedule);self.checked(schedule)
        proposal=self.proposal(owner=schedule)
        source=schedule.entries.get(kind='labor')
        save_entry(self.planner,source,[{'planned_count':Decimal(3),'planned_hours':Decimal(24)} for _ in identities(source)],source.revision)
        target=add_entry(self.planner,schedule,{'kind':'labor','brigade':self.brigade,'rate':Decimal(10)})
        save_entry(self.planner,target,[{'planned_count':Decimal(2),'planned_hours':Decimal(16)} for _ in identities(target)],target.revision)
        preparation.ready(self.manager,schedule)
        with self.assertRaisesMessage(ValidationError,'Нельзя отправить'):smg_transition(self.manager,schedule,'submit')
        self.replace(proposal,self.brigade)
        self.assertEqual(schedule.entries.filter(kind='labor').count(),1)
        target=schedule.entries.get(kind='labor')
        self.assertTrue(all(Decimal(row['planned_count'])==5 and Decimal(row['planned_hours'])==40 for row in target.rows))
        self.assertFalse(review.requests_for(schedule,blocking=True).exists())
        # Canonical key prevents adding the replacement a second time.
        with self.assertRaisesMessage(ValidationError,'уже есть'):add_entry(self.planner,schedule,{'kind':'labor','brigade':self.brigade,'rate':0})
        preparation.ready(self.manager,schedule)
        self.assertEqual(smg_transition(self.manager,schedule,'submit').status,'SUBMITTED')

    def test_fix_rejected_position_in_own_plan_uses_preview_and_preserves_history(self):
        proposal=self.proposal();review.decide(self.hr,proposal,'reject',0,'Дубликат')
        url=reverse('planning:catalogue_fix',args=['gpr',self.version.pk,proposal.pk])
        page=self.client.post(url,{'target':self.brigade.pk,'revision':1,'stage':'preview'})
        self.assertEqual(page.status_code,200);self.assertIsNotNone(page.context['preview'])
        token=page.context['preview']['token']
        response=self.client.post(url,{'target':self.brigade.pk,'revision':1,'stage':'apply','token':token})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.version.resource_allocations.get().brigade,self.brigade)
        proposal.refresh_from_db();self.assertEqual(proposal.status,'REJECTED');self.assertEqual(proposal.comment,'Дубликат')

    def test_manager_create_http_queue_and_readonly_reviewer_source_plan(self):
        self.handoff();self.client.force_login(self.manager)
        response=self.client.post(reverse('planning:catalogue_propose',args=['gpr',self.version.pk,'equipment']),{'name':'Экскаватор новый','months':[str(JAN)]})
        self.assertEqual(response.status_code,302)
        proposal=CatalogueReviewRequest.objects.get()
        self.client.force_login(self.tech)
        page=self.client.get(reverse('resources:catalogue_review_list'))
        self.assertContains(page,'Экскаватор новый')
        url=reverse('resources:catalogue_review_detail',args=[proposal.pk])
        self.assertEqual(self.client.post(url,{'action':'confirm','revision':0}).status_code,302)
        proposal.refresh_from_db();self.assertEqual(proposal.status,'CONFIRMED')
        self.assertEqual(self.client.get(reverse('planning:workspace_edit',args=[self.version.pk])).status_code,403)

    def test_assignment_and_captured_crew_references_are_replaced_without_recalculation(self):
        proposal=self.proposal()
        from apps.resources.position_catalog import position_for
        position=position_for(proposal.brigade)
        assignment=WorkResourceAssignment.objects.create(company=self.company,version=self.version,position=position,
            start_date=JAN,end_date=JAN,count=4,shifts_per_day=1,hours_per_shift=8,
            crew_snapshot=[{'brigade_id':proposal.brigade_id,'position_id':position.pk,'name':proposal.resource.name,'count':1,'is_replacement':False}])
        self.version.resource_allocations.get().delete()
        self.assertTrue(review.requests_for(self.version,blocking=True).exists())
        self.replace(proposal,self.brigade)
        assignment.refresh_from_db();self.assertEqual(assignment.count,4)
        self.assertEqual(assignment.position_id,position_for(self.brigade).pk)
        self.assertEqual(assignment.crew_snapshot[0]['brigade_id'],self.brigade.pk)

    def test_equipment_replacement_preserves_monthly_quantities_and_copied_drafts(self):
        proposal=self.proposal('equipment')
        source=self.version.resource_allocations.get();source.count=3;source.hours=24;source.save()
        target=EquipmentType.objects.create(company=self.company,name='Подтверждённая техника')
        ResourceMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,kind='equipment',equipment_type=target,count=2,hours=16)
        from .workspace_services import WorkspaceService
        other=WorkspaceService.create(self.planner,self.obj,'Копия с новой техникой',JAN,date(2026,1,31)).baseline_version
        ResourceMonthAllocation.objects.create(company=self.company,version=other,month=JAN,kind='equipment',equipment_type=proposal.equipment_type,count=4,hours=32)
        self.assertTrue(review.requests_for(other,blocking=True).exists())
        self.replace(proposal,target)
        self.assertEqual(self.version.resource_allocations.get(kind='equipment').count,5)
        copied=other.resource_allocations.get(kind='equipment');self.assertEqual((copied.count,copied.hours,copied.equipment_type_id),(4,32,target.pk))
        self.assertFalse(review.requests_for(other,blocking=True).exists())

    def test_closed_smg_days_are_preserved_and_nonzero_pending_history_cannot_be_rewritten(self):
        schedule=create_smg(self.planner,self.obj,JAN)
        proposal=self.proposal('equipment',owner=schedule)
        source=schedule.entries.get(kind='equipment')
        target_gear=EquipmentType.objects.create(company=self.company,name='Техника для закрытых дней')
        target=add_entry(self.planner,schedule,{'kind':'equipment','equipment':target_gear,'rate':Decimal(0)})
        save_entry(self.planner,target,[{'planned_count':Decimal(2),'planned_machine_hours':Decimal(16)} for _ in identities(target)],target.revision)
        target.refresh_from_db();frozen=deepcopy(target.rows[:2])
        schedule.locked_before=date(2026,1,3);schedule.save(update_fields=['locked_before'])
        self.replace(proposal,target_gear)
        target=schedule.entries.get(kind='equipment');self.assertEqual(target.rows[:2],frozen)
        other=create_smg(self.planner,self.obj,date(2026,2,1))
        proposal=self.proposal('equipment',owner=other,name='Новая закрытая техника')
        source=other.entries.get(kind='equipment')
        save_entry(self.planner,source,[{'planned_count':Decimal(1),'planned_machine_hours':Decimal(8)} for _ in identities(source)],source.revision)
        other.locked_before=date(2026,2,3);other.save(update_fields=['locked_before'])
        token,_=review.preview(self.tech,proposal,target_gear)
        with self.assertRaisesMessage(ValidationError,'закрытых днях'):
            review.decide(self.tech,proposal,'replace',proposal.revision,target=target_gear,token=token)
        proposal.refresh_from_db();self.assertEqual(proposal.status,'PENDING')

    def test_review_cannot_rewrite_a_submitted_plan_and_duplicate_decisions_are_rejected(self):
        proposal=self.proposal()
        from .models import GlobalPlanVersion
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='SUBMITTED')
        with self.assertRaisesMessage(ValidationError,'зафиксированном плане'): self.confirm(proposal)
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='DRAFT')
        self.confirm(proposal)
        with self.assertRaises(ValidationError): self.confirm(proposal)

    def test_manager_saves_pending_equipment_and_labor_in_same_gpr_formset(self):
        self.handoff();self.proposal();gear=self.proposal('equipment')
        self.client.force_login(self.manager)
        url=reverse('planning:workspace_edit',args=[self.version.pk])+'?month='+str(JAN)
        page=self.client.get(url)
        data={'action':'save'}
        for key,forms in [('works',page.context['work_forms']),('resources',page.context['resource_forms'])]:
            data[key+'-TOTAL_FORMS']=str(forms.total_form_count());data[key+'-INITIAL_FORMS']=str(forms.initial_form_count())
            for form in forms:
                for field in form:
                    if field.value() is not None and field.name != 'DELETE' and not field.field.disabled:
                        data[field.html_name]=field.value()
                if key=='resources' and form.instance.kind=='equipment': data[form.prefix+'-count']='2'
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302, response.context and {'works':response.context['work_forms'].errors,'resources':response.context['resource_forms'].errors})
        self.assertEqual(self.version.resource_allocations.get(equipment_type_id=gear.equipment_type_id).count,2)

    def test_pending_positions_are_available_only_within_own_plan_and_readonly_routes_reject_cleanly(self):
        proposal=self.proposal()
        from .daily_schedule_table_forms import AddEntryForm
        from .work_resource_views import AssignmentForm
        from apps.resources.position_catalog import position_for
        self.assertIn(proposal.brigade,AddEntryForm(schedule=self.version,user=self.planner).fields['brigade'].queryset)
        self.assertIn(position_for(proposal.brigade),AssignmentForm(owner=self.version).fields['position'].queryset)
        other=create_smg(self.planner,self.obj,JAN)
        self.assertNotIn(proposal.brigade,AddEntryForm(schedule=other,user=self.planner).fields['brigade'].queryset)
        from .models import GlobalPlanVersion
        GlobalPlanVersion.objects.filter(pk=self.version.pk).update(status='SUBMITTED')
        url=reverse('planning:catalogue_propose',args=['gpr',self.version.pk,'equipment'])
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,{'name':'Нельзя изменить'}).status_code,403)

    def test_admin_cannot_skip_catalogue_gate_and_unconfirmed_positions_are_not_employee_choices(self):
        self.handoff();self.checked();proposal=self.proposal()
        preparation.ready(self.manager,self.version)
        admin=User.objects.create_user(username='review-admin-gate',company=self.company,role=Role.objects.get(code='ADMIN'))
        with self.assertRaisesMessage(ValidationError,'Нельзя отправить'):preparation.require_submission(admin,self.version)
        from apps.resources.forms import EmployeeForm
        from apps.resources.position_catalog import position_for
        self.assertNotIn(position_for(proposal.brigade),EmployeeForm().fields['position'].queryset)

    @override_settings(TIME_ZONE='Europe/Moscow')
    def test_decision_history_uses_local_time_and_readable_labels(self):
        proposal=self.proposal();self.confirm(proposal)
        proposal.refresh_from_db()
        proposal.history=[{'at':'2026-10-29T13:00:00+00:00','name':'Ответственный','action':'confirm','comment':''}]
        proposal.save(update_fields=['history'])
        self.client.force_login(self.hr)
        page=self.client.get(reverse('resources:catalogue_review_detail',args=[proposal.pk]))
        self.assertContains(page,'29.10.2026 16:00')
        self.assertContains(page,'Подтверждено')
