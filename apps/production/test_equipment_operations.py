from datetime import timedelta
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role, User
from apps.works.models import ProjectWork
from . import test_dispatch as fixtures
from . import equipment_operations as operations
from .models import EquipmentAssignment, EquipmentStateChange, EquipmentActivity, EquipmentFact, FactSectionDay, EmployeeActivity


class EquipmentOperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.DispatcherTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.clock = patch('django.utils.timezone.localdate', return_value=self.day+timedelta(days=1))
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.operator = self.users['MECHANIZATION_DISPATCHER']
        self.client.force_login(self.operator)

    def assign(self, **changes):
        values = dict(company=self.company, equipment=self.machine, construction_object=self.obj,
            start_date=self.day, shift=1, work=self.work, activity='Работа по действующему назначению')
        values.update(changes)
        return operations.create_assignment(self.users['ADMIN'], EquipmentAssignment(**values))

    def payload(self, day=None):
        day = day or self.day
        response = self.client.get(self.url, {'construction_object': self.obj.pk, 'date': day.isoformat(), 'section': 'mechanization'})
        self.assertEqual(response.status_code, 200)
        data = {'construction_object': self.obj.pk, 'date': day.isoformat(), 'section': 'mechanization',
                'revision': response.context['revision'], 'equipment_assignment_version': response.context['equipment_assignment_version'], 'action': 'confirm'}
        for group in response.context['groups']:
            fs = group['formset']
            for field in fs.management_form:
                data[field.html_name] = field.value()
            for form in fs:
                for field in form:
                    if field.name != 'DELETE':
                        value = field.value()
                        data[field.html_name] = str(value) if value is not None and value is not False else ''
        return data, response

    def test_preview_does_not_write_facts_and_unchanged_confirmation_saves_once(self):
        assignment = self.assign()
        data, response = self.payload()
        self.assertEqual(response.context['prepared_equipment_count'], 1)
        self.assertContains(response, 'Подготовлено из назначения')
        self.assertFalse(EquipmentActivity.objects.exists()); self.assertFalse(EquipmentFact.objects.exists())
        result = self.client.post(self.url, data)
        self.assertEqual(result.status_code, 302, result.context and result.context.get('error'))
        fact = EquipmentActivity.objects.get()
        self.assertEqual(fact.standing_assignment_id, assignment.pk)
        self.assertEqual(fact.state, 'WORKING')
        self.assertEqual(EquipmentFact.objects.get().actual_count, 1)
        self.assertIsNotNone(FactSectionDay.objects.get(section='mechanization').confirmed_at)
        again, _ = self.payload(); self.assertEqual(self.client.post(self.url, again).status_code, 302)
        self.assertEqual(EquipmentActivity.objects.get().pk, fact.pk)
        self.assertEqual(EquipmentFact.objects.count(), 1)

    def test_daily_exception_does_not_change_following_days(self):
        self.assign()
        data, _ = self.payload()
        data.update({'equipment-0-state': 'REPAIR', 'equipment-0-activity': 'Ремонт в течение этой смены'})
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(EquipmentActivity.objects.get().state, 'REPAIR')
        self.assertEqual(EquipmentFact.objects.get().actual_count, 0)
        self.assertEqual(EquipmentStateChange.objects.get().state, 'WORKING')
        next_day = self.day+timedelta(days=1)
        tomorrow, _ = self.payload(next_day)
        self.assertEqual(tomorrow['equipment-0-state'], 'WORKING')
        self.assertEqual(self.client.post(self.url, tomorrow).status_code, 302)
        self.assertEqual(EquipmentActivity.objects.get(date=next_day).state, 'WORKING')

    def test_state_history_applies_from_boundary_and_saved_fact_remains_a_snapshot(self):
        assignment = self.assign()
        data, _ = self.payload(); self.assertEqual(self.client.post(self.url, data).status_code, 302)
        operations.change_state(self.operator, assignment, 1, self.day, 'REPAIR', 'Ремонт гидравлики')
        fact = EquipmentActivity.objects.get(); self.assertEqual(fact.state, 'WORKING')
        _, page = self.payload()
        self.assertContains(page, 'Отклонение на день')
        self.assertIsNotNone(FactSectionDay.objects.get(section='mechanization').confirmed_at)
        tomorrow, _ = self.payload(self.day+timedelta(days=1))
        self.assertEqual(tomorrow['equipment-0-state'], 'REPAIR')
        self.assertEqual(tomorrow['equipment-0-activity'], 'Ремонт гидравлики')
        self.assertEqual(tomorrow['equipment-0-work'], '')
        self.assertEqual(self.client.post(self.url, tomorrow).status_code, 302)

    def test_stale_status_and_added_assignment_reject_confirmation_atomically(self):
        old_empty, _ = self.payload()
        assignment = self.assign()
        self.assertEqual(self.client.post(self.url, old_empty).status_code, 400)
        data, _ = self.payload()
        operations.change_state(self.operator, assignment, 1, self.day, 'IDLE', 'Ожидание работ')
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Назначения или состояния техники изменились', status_code=400)
        self.assertFalse(EquipmentActivity.objects.exists())
        self.assertFalse(EquipmentFact.objects.exists())
        self.assertEqual(FactSectionDay.objects.get(section='mechanization').revision, 0)

    def test_omission_or_deletion_is_not_confirmation_of_active_assignment(self):
        self.assign()
        for delete in [False, True]:
            data, _ = self.payload()
            if delete:
                data['equipment-0-DELETE'] = 'on'
            else:
                data.update({'equipment-TOTAL_FORMS': '0', 'equipment-INITIAL_FORMS': '0'})
            response = self.client.post(self.url, data)
            self.assertEqual(response.status_code, 400)
            self.assertFalse(EquipmentActivity.objects.exists())

    def test_period_overlap_close_and_next_assignment(self):
        assignment = self.assign()
        with self.assertRaises(ValidationError):
            self.assign(construction_object=self.other, general=True, work=None)
        operations.close_assignment(self.operator, assignment, 1, self.day)
        self.assertFalse(operations.prepare(self.obj, self.day+timedelta(days=1))[0])
        second = self.assign(construction_object=self.other, start_date=self.day+timedelta(days=1), work=None, general=True)
        self.assertNotEqual(assignment.pk, second.pk)
        self.assertEqual(EquipmentAssignment.objects.count(), 2)

    def test_recurring_work_change_preserves_previous_period(self):
        assignment = self.assign()
        other_work = ProjectWork.objects.create(company=self.company, section=self.work.section, name='Другая работа', kind='SIMPLE', unit='м')
        changed = operations.change_assignment(self.operator, assignment, 1, self.day+timedelta(days=1),
            work=other_work, work_item=None, general=False, activity='Работа на новом участке')
        self.assertNotEqual(changed.pk, assignment.pk)
        assignment.refresh_from_db(); self.assertEqual(assignment.end_date, self.day)
        self.assertEqual(operations.prepare(self.obj, self.day)[0][(self.machine.pk, 1)]['values']['work'], self.work.pk)
        self.assertEqual(operations.prepare(self.obj, self.day+timedelta(days=1))[0][(self.machine.pk, 1)]['values']['work'], other_work.pk)

    def test_machine_and_crew_locations_are_not_overridden(self):
        EmployeeActivity.objects.create(company=self.company, construction_object=self.other, date=self.day,
            shift=1, employee=self.employee, position=self.employee.position, equipment=self.machine, general=True)
        with self.assertRaises(ValidationError):
            self.assign()
        self.assertFalse(EquipmentAssignment.objects.exists())
        self.assertFalse(EquipmentStateChange.objects.exists())

    def test_shift_boundary_and_future_state_validation(self):
        first = self.assign()
        second = self.assign(shift=2)
        operations.change_state(self.operator, second, 1, self.day, 'REPAIR', 'Ремонт со второй смены')
        baseline, _ = operations.prepare(self.obj, self.day)
        self.assertEqual(baseline[(self.machine.pk, 1)]['values']['state'], 'WORKING')
        self.assertEqual(baseline[(self.machine.pk, 2)]['values']['state'], 'REPAIR')
        future, _ = operations.prepare(self.obj, self.day+timedelta(days=1))
        self.assertEqual(future[(self.machine.pk, 1)]['values']['state'], 'REPAIR')
        with self.assertRaises(ValidationError):
            operations.change_state(self.operator, first, 1, self.day+timedelta(days=2), 'WORKING', '')

    def test_management_permissions_company_and_object_scope_and_stale_revision(self):
        assignment = self.assign()
        url = reverse('production:equipment_assignment_list')
        data = dict(construction_object=self.obj.pk, date=str(self.day), assignment=assignment.pk,
            action='state', effective_date=str(self.day), state='REPAIR', reason='Ремонт', revision=1)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 400)
        self.assertEqual(self.client.get(url, {'construction_object': self.other.pk, 'date': str(self.day)}).context['selector'].is_valid(), False)
        for role in ['DISPATCHER', 'PERSONNEL_DISPATCHER']:
            self.client.force_login(self.users[role]); self.assertEqual(self.client.post(url, data).status_code, 403)
        self.client.force_login(self.operator)
        foreign = self.assign(construction_object=self.other, shift=2, work=None, general=True)
        data.update(assignment=foreign.pk)
        self.assertEqual(self.client.post(url, data).status_code, 404)
        history = self.client.get(reverse('production:equipment_history_detail', args=[self.machine.pk]))
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, 'Ремонт')
        self.assertNotContains(history, '>Other<')

    def test_legacy_fact_is_not_replaced_without_confirmation(self):
        self.assign()
        EquipmentFact.objects.create(company=self.company, construction_object=self.obj, date=self.day,
            equipment_type=self.type, actual_count=7)
        data, _ = self.payload()
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertEqual(EquipmentFact.objects.get().actual_count, 7)
        self.assertFalse(EquipmentActivity.objects.exists())
        data['replace_legacy'] = 'on'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(EquipmentFact.objects.get().actual_count, 1)

    def test_assignment_audit_failure_rolls_back_state_and_period(self):
        with patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):
                self.assign()
        self.assertFalse(EquipmentAssignment.objects.exists())
        self.assertFalse(EquipmentStateChange.objects.exists())

    def test_unused_mistake_can_be_cancelled_with_history_and_replaced(self):
        assignment = self.assign()
        operations.cancel_assignment(self.operator, assignment, 1, 'Выбрана неверная машина')
        assignment.refresh_from_db(); self.assertIsNotNone(assignment.cancelled_at)
        self.assertEqual(EquipmentStateChange.objects.get().is_cancelled, True)
        self.assertFalse(operations.prepare(self.obj, self.day)[0])
        replacement = self.assign()
        self.assertNotEqual(replacement.pk, assignment.pk)
        self.assertEqual(operations.prepare(self.obj, self.day)[0][(self.machine.pk, 1)]['assignment'].pk, replacement.pk)
        self.assertEqual(EquipmentStateChange.objects.count(), 2)
        assignment.full_clean()

    def test_used_assignment_cannot_be_cancelled(self):
        assignment = self.assign()
        data, _ = self.payload(); self.assertEqual(self.client.post(self.url, data).status_code, 302)
        with self.assertRaises(ValidationError):
            operations.cancel_assignment(self.operator, assignment, 1, 'Отмена')
        assignment.refresh_from_db(); self.assertIsNone(assignment.cancelled_at)
        self.assertFalse(EquipmentStateChange.objects.get().is_cancelled)

    def test_distinct_unnumbered_units_and_repeated_shifts_are_counted_once(self):
        from apps.resources.models import Equipment
        self.machine.plate_number = ''; self.machine.save()
        second = Equipment.objects.create(company=self.company, type=self.type, name='Вторая машина')
        self.assign(); self.assign(shift=2); self.assign(equipment=second)
        data, _ = self.payload()
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(EquipmentActivity.objects.count(), 3)
        self.assertEqual(sum(EquipmentFact.objects.values_list('actual_count', flat=True)), 2)

    def test_inactive_assigned_unit_is_not_silently_omitted(self):
        self.assign()
        self.machine.is_active = False; self.machine.save()
        data, response = self.payload()
        self.assertEqual(response.context['prepared_equipment_count'], 1)
        self.assertEqual(self.client.post(self.url, data).status_code, 302)

    def test_personnel_cannot_attach_crew_to_a_machine_assigned_elsewhere(self):
        self.assign(construction_object=self.other, work=None, general=True)
        data = fixtures.DispatcherTests.payload(self, 'PERSONNEL_DISPATCHER', 'labor')
        data.update({'labor-0-employee': self.employee.pk, 'labor-0-equipment': self.machine.pk, 'labor-0-general': 'on'})
        self.assertEqual(self.client.post(self.url, data).status_code, 400)
        self.assertFalse(EmployeeActivity.objects.exists())

    def test_new_assignment_preserves_physical_repair_state(self):
        assignment = self.assign()
        operations.change_state(self.operator, assignment, 1, self.day, 'REPAIR', 'Ремонт двигателя')
        operations.close_assignment(self.operator, assignment, 2, self.day)
        following = self.assign(start_date=self.day+timedelta(days=1))
        values = operations.prepare(self.obj, following.start_date)[0][(self.machine.pk, 1)]['values']
        self.assertEqual(values['state'], 'REPAIR')
        self.assertEqual(values['activity'], 'Ремонт двигателя')
        self.assertEqual(EquipmentStateChange.objects.count(), 1)

    def test_existing_confirmed_machine_status_bootstraps_first_assignment(self):
        prior = self.day-timedelta(days=1)
        EquipmentActivity.objects.create(company=self.company, construction_object=self.obj, date=prior,
            equipment=self.machine, state='REPAIR', activity='Подтверждённый ремонт')
        from django.utils import timezone
        FactSectionDay.objects.create(company=self.company, construction_object=self.obj, date=prior,
            section='mechanization', confirmed_at=timezone.now(), confirmed_by=self.operator)
        self.assign()
        self.assertEqual(EquipmentStateChange.objects.get().state, 'REPAIR')
        self.assertEqual(operations.prepare(self.obj, self.day)[0][(self.machine.pk, 1)]['values']['activity'], 'Подтверждённый ремонт')

    def test_cancel_mistake_after_draft_releases_unit_and_preserves_audit_copy(self):
        assignment = self.assign()
        data, _ = self.payload(); data['action'] = 'save'
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        saved_revision = FactSectionDay.objects.get(section='mechanization').revision
        fact = EquipmentActivity.objects.get()
        operations.cancel_assignment(self.operator, assignment, 1, 'Машина выбрана ошибочно')
        self.assertFalse(EquipmentActivity.objects.exists())
        self.assertFalse(EquipmentFact.objects.exists())
        from apps.audit.models import AuditLog
        audit = AuditLog.objects.filter(entity_type='production.EquipmentAssignment', entity_id=str(assignment.pk)).latest('pk')
        self.assertEqual(audit.new_value['removed_draft_facts'][0]['id'], fact.pk)
        self.assertIsNone(FactSectionDay.objects.get(section='mechanization').confirmed_at)
        self.assertGreater(FactSectionDay.objects.get(section='mechanization').revision, saved_revision)
        self.assertNotEqual(self.assign().pk, assignment.pk)

    def test_cancellation_cannot_erase_repair_inherited_by_another_assignment(self):
        origin = self.assign()
        operations.change_state(self.operator, origin, 1, self.day, 'REPAIR', 'Ремонт двигателя')
        following = self.assign(shift=2)
        with self.assertRaises(ValidationError):
            operations.cancel_assignment(self.operator, origin, 2, 'Отмена исходного назначения')
        self.assertEqual(operations.prepare(self.obj, self.day)[0][(self.machine.pk, 2)]['values']['state'], 'REPAIR')
        operations.cancel_assignment(self.operator, following, 1, 'Отмена зависимого назначения')
        operations.cancel_assignment(self.operator, origin, 2, 'Отмена ошибочной цепочки')
        self.assertFalse(operations.prepare(self.obj, self.day)[0])
