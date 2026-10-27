from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4
from django.db import transaction
from django.db.models import F
from django.test import TestCase,Client
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import User,Role,Company
from apps.projects.models import Project,ConstructionObject,Section
from apps.resources.models import Equipment
from apps.personnel.models import CampStay
from core.fact_history import history_actor
from . import test_dispatch as fixtures
from .models import DailyFact,EmployeeActivity,EquipmentActivity
from .dispatch_models import EmployeeWorkLink
from .history_models import FactRevision
from . import transport
from .transport_models import EquipmentDayRecord


class FactHistoryTests(TestCase):
    setUpTestData=classmethod(fixtures.DispatcherTests.setUpTestData.__func__)

    def setUp(self):
        self.first=self.users['DISPATCHER'];self.admin=self.users['ADMIN']
        self.second=User.objects.create_user(username='history-colleague',company=self.company,role=Role.objects.get(code='DISPATCHER'))
        self.second.assigned_objects.add(self.obj)

    def revisions(self,model,pk):return FactRevision.objects.filter(entity_type=model._meta.label_lower,entity_id=str(pk)).order_by('pk')
    def fact(self):
        with history_actor(self.first):
            return DailyFact.objects.create(company=self.company,project_work=self.work,date=self.day,actual_quantity=5,reported_by=self.first)

    def test_changes_preserve_original_author_values_each_editor_and_all_revisions(self):
        row=self.fact()
        for actor,value in [(self.second,4),(self.first,3),(self.admin,2)]:
            with history_actor(actor):row.actual_quantity=value;row.save()
        row.refresh_from_db();self.assertEqual(row.reported_by_id,self.first.pk)
        changes=list(self.revisions(DailyFact,row.pk))
        self.assertEqual([c.operation for c in changes],['CREATE','UPDATE','UPDATE','UPDATE'])
        self.assertEqual([c.actor_id for c in changes],[self.first.pk,self.second.pk,self.first.pk,self.admin.pk])
        self.assertEqual(changes[1].before['fields']['actual_quantity'],'5.000')
        self.assertEqual(changes[1].after['fields']['actual_quantity'],'4.000')
        self.assertEqual(changes[-1].before['fields']['actual_quantity'],'3.000')
        self.assertIn(self.work.name,changes[0].after['references']['project_work_id'])

    def test_deletion_preserves_full_before_snapshot_and_history_remains_accessible(self):
        row=self.fact();pk=row.pk
        with history_actor(self.second):row.delete()
        self.assertFalse(DailyFact.objects.filter(pk=pk).exists())
        revision=self.revisions(DailyFact,pk).last()
        self.assertEqual(revision.operation,'DELETE');self.assertEqual(revision.before['fields']['actual_quantity'],'5.000');self.assertIsNone(revision.after)
        self.client.force_login(self.second)
        response=self.client.get(reverse('production:fact_history'),{'entity':'production.dailyfact','id':pk})
        self.assertEqual(response.status_code,200);self.assertContains(response,'Удалено');self.assertContains(response,self.work.name)

    def test_bulk_update_bulk_create_and_cascaded_links_are_journaled(self):
        with history_actor(self.first):
            rows=EmployeeActivity.objects.bulk_create([EmployeeActivity(company=self.company,construction_object=self.obj,date=self.day,employee=self.employee,position=self.employee.position,general=True)])
            activity=rows[0]
            link=EmployeeWorkLink.objects.create(company=self.company,activity=activity,work=self.work)
        with history_actor(self.second):
            EmployeeActivity.objects.filter(pk=activity.pk).update(activity='Новое пояснение')
        self.assertEqual(self.revisions(EmployeeActivity,activity.pk).last().after['fields']['activity'],'Новое пояснение')
        with history_actor(self.admin):EmployeeActivity.objects.filter(pk=activity.pk).delete()
        self.assertEqual(self.revisions(EmployeeWorkLink,link.pk).last().operation,'DELETE')
        self.assertEqual(self.revisions(EmployeeActivity,activity.pk).last().operation,'DELETE')
        self.assertEqual(self.revisions(EmployeeWorkLink,link.pk).last().before['references']['work_id'],str(self.work))

    def test_transaction_rollback_never_leaves_half_a_fact_or_half_a_history(self):
        row=self.fact();before=self.revisions(DailyFact,row.pk).count()
        with self.assertRaises(RuntimeError):
            with transaction.atomic(),history_actor(self.second):
                row.actual_quantity=4;row.save();raise RuntimeError('Отмена всей операции')
        row.refresh_from_db();self.assertEqual(row.actual_quantity,5);self.assertEqual(self.revisions(DailyFact,row.pk).count(),before)
        with patch('core.fact_history.append_revision',side_effect=RuntimeError('Историю записать не удалось')):
            with self.assertRaises(RuntimeError):
                with history_actor(self.second):row.actual_quantity=3;row.save()
        row.refresh_from_db();self.assertEqual(row.actual_quantity,5)

    def test_failed_bulk_history_write_rolls_back_the_entire_bulk_update(self):
        row=self.fact()
        with patch('core.fact_history.append_revision',side_effect=RuntimeError('Недоступен журнал')):
            with self.assertRaises(RuntimeError):DailyFact.objects.filter(pk=row.pk).update(actual_quantity=1)
        row.refresh_from_db();self.assertEqual(row.actual_quantity,5)

    def test_history_cannot_be_changed_or_deleted_through_application_models_or_http(self):
        row=self.fact();revision=self.revisions(DailyFact,row.pk).first()
        for attempt in [lambda:revision.delete(),lambda:FactRevision.objects.filter(pk=revision.pk).delete(),lambda:FactRevision.objects.filter(pk=revision.pk).update(actor_name='Подмена')]:
            with self.assertRaises(ValueError):attempt()
        revision.actor_name='Подмена'
        with self.assertRaises(ValueError):revision.save()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('production:fact_history'),{}).status_code,405)

    def test_existing_record_first_edit_captures_baseline_and_actor_context_is_reset(self):
        row=self.fact()
        with history_actor(self.second):row.comment='Правка коллеги';row.save()
        row.comment='Фоновое обновление';row.save()
        self.assertIsNone(self.revisions(DailyFact,row.pk).last().actor_id)
        revision=self.revisions(DailyFact,row.pk).first()
        self.assertEqual(revision.actor_name,self.first.username)
        row.updated_at=row.updated_at;before=self.revisions(DailyFact,row.pk).count();row.save()
        self.assertEqual(self.revisions(DailyFact,row.pk).count(),before)

    def test_a_preexisting_unjournaled_record_keeps_its_first_before_state(self):
        row=DailyFact(company=self.company,project_work=self.work,date=self.day,actual_quantity=5,actual_value=50,created_at=timezone.now(),updated_at=timezone.now())
        row.save_base(raw=True)
        self.assertFalse(self.revisions(DailyFact,row.pk).exists())
        with history_actor(self.second):row.actual_quantity=4;row.save()
        revision=self.revisions(DailyFact,row.pk).get()
        self.assertEqual(revision.operation,'UPDATE');self.assertEqual(revision.before['fields']['actual_quantity'],'5.000')
        self.assertEqual(revision.after['fields']['actual_quantity'],'4.000')

    def test_editor_name_survives_user_deletion_and_bulk_update_cannot_rewrite_history(self):
        row=self.fact()
        with history_actor(self.second):row.comment='Правка';row.save()
        editor=self.second.username;self.second.delete()
        revision=self.revisions(DailyFact,row.pk).last()
        self.assertIsNone(revision.actor_id);self.assertEqual(revision.actor_name,editor)
        revision.reason='Подмена'
        with self.assertRaises(ValueError):FactRevision.objects.bulk_update([revision],['reason'])

    def test_global_superuser_can_audit_cross_company_admin_corrections(self):
        row=self.fact()
        company=Company.objects.create(name='Компания глобального администратора')
        admin=User.objects.create_user(username='global-history-superuser',company=company,is_superuser=True,is_staff=True)
        with history_actor(admin):row.comment='Глобальное исправление';row.save()
        revision=self.revisions(DailyFact,row.pk).last()
        self.assertEqual(revision.company_id,self.company.pk);self.assertEqual(revision.actor_id,admin.pk);self.assertEqual(revision.actor_role,'ADMIN')

    def test_transport_corrections_and_clear_keep_original_contributor_and_all_values(self):
        first=self.users['MECHANIZATION_DISPATCHER'];second=User.objects.create_user(username='transport-editor',company=self.company,role=Role.objects.get(code='MECHANIZATION_DISPATCHER'));second.assigned_objects.add(self.obj)
        camp=self.housing_camp
        transport.move(first,camp,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(first,camp,self.day))
        transport.save_day(first,camp,self.day,[self.machine.pk],'WORKED',comment='Первая запись',signed=transport.snapshot(first,camp,self.day))
        record=EquipmentDayRecord.objects.get();pk=record.pk
        transport.save_day(second,camp,self.day,[self.machine.pk],'IDLE',comment='Исправление',signed=transport.snapshot(second,camp,self.day))
        record.refresh_from_db();self.assertEqual(record.recorded_by_id,first.pk)
        change=self.revisions(EquipmentDayRecord,pk).last();self.assertEqual(change.actor_id,second.pk)
        self.assertEqual(change.before['fields']['status'],'WORKED');self.assertEqual(change.after['fields']['status'],'IDLE')
        transport.save_day(second,camp,self.day,[self.machine.pk],'CLEAR',signed=transport.snapshot(second,camp,self.day))
        self.assertEqual(self.revisions(EquipmentDayRecord,pk).last().before['fields']['comment'],'Исправление')

    def test_company_object_and_direction_scope_are_enforced_in_history(self):
        self.fact();self.client.force_login(self.first)
        allowed=self.client.get(reverse('production:fact_history'));self.assertEqual(allowed.context['page_obj'].paginator.count,1)
        self.first.assigned_objects.clear()
        response=self.client.get(reverse('production:fact_history'));self.assertEqual(response.context['page_obj'].paginator.count,0)
        self.client.force_login(self.users['MECHANIZATION_DISPATCHER'])
        response=self.client.get(reverse('production:fact_history'),{'entity':'production.dailyfact'});self.assertEqual(response.context['page_obj'].paginator.count,0)
        company=Company.objects.create(name='Другая компания');other=User.objects.create_user(username='other-history-admin',company=company,role=Role.objects.get(code='ADMIN'))
        self.client.force_login(other);response=self.client.get(reverse('production:fact_history'));self.assertEqual(response.context['page_obj'].paginator.count,0)

    def test_middleware_records_the_actual_http_editor(self):
        self.client.force_login(self.users['MECHANIZATION_DISPATCHER'])
        camp=self.housing_camp;operator=self.users['MECHANIZATION_DISPATCHER']
        transport.move(operator,camp,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(operator,camp,self.day))
        response=self.client.post(reverse('production:transport_board'),{'camp':camp.pk,'date':str(self.day),'action':'DAY','equipment':[self.machine.pk],'status':'WORKED','signed':transport.snapshot(operator,camp,self.day)})
        self.assertEqual(response.status_code,302)
        record=EquipmentDayRecord.objects.get();self.assertEqual(self.revisions(EquipmentDayRecord,record.pk).last().actor_id,operator.pk)


class CollaborativeProductionHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from .test_production_entry import ProductionEntryTests
        ProductionEntryTests.setUpTestData.__func__(cls)
    payload=fixtures.DispatcherTests.payload
    def test_colleague_edit_is_saved_with_original_author_and_new_editor_in_history(self):
        from .test_production_entry import ProductionEntryTests
        first=self.payload('DISPATCHER','works');prefix=ProductionEntryTests.row(self,first,self.work);first[prefix+'actual_quantity']='5'
        self.client.force_login(self.users['DISPATCHER']);self.assertEqual(self.client.post(self.url,first).status_code,302)
        record=DailyFact.objects.get(project_work=self.work);self.assertEqual(record.reported_by_id,self.users['DISPATCHER'].pk)
        second=self.payload('SECOND','works');prefix=ProductionEntryTests.row(self,second,self.work);second[prefix+'actual_quantity']='4';second[prefix+'deviation_reason']=self.reason.pk
        self.client.force_login(self.second);self.assertEqual(self.client.post(self.url,second).status_code,302)
        record.refresh_from_db();self.assertEqual(record.actual_quantity,4);self.assertEqual(record.reported_by_id,self.users['DISPATCHER'].pk)
        revision=FactRevision.objects.filter(entity_type='production.dailyfact',entity_id=str(record.pk),operation='UPDATE').latest('pk')
        self.assertEqual(revision.actor_id,self.second.pk);self.assertEqual(revision.before['fields']['actual_quantity'],'5.000');self.assertEqual(revision.after['fields']['actual_quantity'],'4.000')

