from copy import deepcopy
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError, PermissionDenied
from django.db.models.deletion import ProtectedError
from apps.accounts.models import Company
from apps.projects.models import Project, ConstructionObject, Section
from apps.works.models import ProjectWork, ProjectWorkItem
from . import test_work_resources as fixtures, work_resources, resource_targets, preparation
from .models import WorkResourceAssignment, WorkResourceTarget, WorkMonthAllocation
from .workspace_services import build_workspace_snapshot, WorkspaceService
from .resource_editor import serialize
from .estimate_review import signature
from .independent_smg import create, revise
from .daily_schedule import overlay_schedules
from .test_daily_schedule import activate

JAN = fixtures.JAN


class SharedWorkResourceTests(TestCase):
    setUpTestData = classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner = fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.composite,quantity=1)
        self.client.force_login(self.manager)
        self.url = reverse('planning:preparation_add_assignment',args=[self.version.pk])

    def tokens(self):return [f'item:{self.composite.pk}:{item.pk}' for item in [self.a,self.b]]

    def data(self, kind='equipment', **changes):
        values = {'resource_kind':kind,'target_scope':'works','targets':self.tokens(),
            'start_date':'2026-01-01','end_date':'2026-01-02','count':2,'shifts_per_day':2,'hours_per_shift':8,
            'equipment_type':self.equipment.pk,'include_crew':'on','daily_liters':0,'fuel_price':0}
        if kind=='labor':values.update(equipment_type='',position=self.position.pk,include_crew='')
        if kind=='fuel':values.update(equipment_type='',fuel_type='DIESEL',daily_liters='12.5',fuel_price=60)
        return {**values, **changes}

    def make(self, **changes):
        response = self.client.post(self.url,self.data(**changes))
        self.assertEqual(response.status_code,200,response.content)
        return self.version.resource_assignments.get(pk=response.json()['assignment']['id'])

    def test_one_allocation_visible_on_both_targets_and_counted_once(self):
        assignment = self.make()
        self.assertEqual(self.version.resource_assignments.count(),1)
        self.assertEqual(assignment.extra_targets.count(),1)
        result = serialize(assignment)
        self.assertTrue(result['shared'])
        self.assertEqual({t['work_item_id'] for t in result['targets']},{self.a.pk,self.b.pk})
        snapshot = build_workspace_snapshot(self.version)
        equipment = snapshot['resources']['equipment']
        self.assertEqual(len(equipment),2)
        self.assertEqual(sum(Decimal(r['planned_count']) for r in equipment),4)
        self.assertEqual(sum(Decimal(r['planned_machine_hours']) for r in equipment),64)
        for row in equipment:self.assertEqual(len(row['work_targets']),2)
        operators = [r for r in snapshot['resources']['labor'] if r['position_id']==self.position.pk]
        self.assertEqual(sum(Decimal(r['planned_count']) for r in operators),8)
        self.assertEqual(len(snapshot['resource_assignments']),1)
        self.assertEqual(len(snapshot['resource_assignments'][0]['work_targets']),2)
        self.assertEqual(snapshot['resources'],build_workspace_snapshot(self.version)['resources'])

    def test_people_and_fuel_are_shared_not_multiplied(self):
        for kind in ['labor','fuel']:
            response = self.client.post(self.url,self.data(kind))
            self.assertEqual(response.status_code,200,response.content)
        snapshot = build_workspace_snapshot(self.version)
        self.assertEqual(sum(Decimal(r['planned_liters']) for r in snapshot['resources']['fuel']),25)
        self.assertEqual(sum(Decimal(r['planned_count']) for r in snapshot['resources']['labor']),8)

    def test_edit_links_then_move_to_object_keeps_one_record(self):
        assignment = self.make()
        response = self.client.post(self.url,self.data(assignment_id=assignment.pk,targets=self.tokens()[1:],count=3))
        self.assertEqual(response.status_code,200,response.content)
        assignment.refresh_from_db()
        self.assertEqual(resource_targets.identities(assignment),[(self.composite.pk,self.b.pk)])
        self.assertEqual(assignment.count,3)
        self.assertFalse(assignment.extra_targets.exists())
        response = self.client.post(self.url,self.data(assignment_id=assignment.pk,target_scope='object',targets=[]))
        self.assertEqual(response.status_code,200,response.content)
        assignment.refresh_from_db();self.assertIsNone(assignment.work_id)
        self.assertFalse(assignment.extra_targets.exists())
        self.assertEqual(self.version.resource_assignments.count(),1)

    def test_simple_work_and_child_can_share_same_resource(self):
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=self.simple,quantity=1)
        assignment = self.make(targets=[self.tokens()[0],f'work:{self.simple.pk}'])
        self.assertEqual(set(resource_targets.identities(assignment)),{(self.composite.pk,self.a.pk),(self.simple.pk,None)})

    def test_invalid_or_unplanned_targets_are_atomic(self):
        assignment = self.make()
        before = resource_targets.identities(assignment)
        wrong = ProjectWorkItem.objects.create(company=self.company,project_work=self.composite,name='Not selected',unit='м')
        WorkMonthAllocation.objects.filter(version=self.version).update(selected_item_ids=[self.a.pk,self.b.pk])
        for selected in [[],[self.tokens()[0],self.tokens()[0]],[f'work:{self.composite.pk}'],
                         [f'item:{self.simple.pk}:{self.b.pk}'],[self.tokens()[0],f'item:{self.composite.pk}:{wrong.pk}']]:
            with self.subTest(selected=selected):
                response = self.client.post(self.url,self.data(assignment_id=assignment.pk,targets=selected,count=5))
                self.assertEqual(response.status_code,400)
                assignment.refresh_from_db();self.assertEqual(assignment.count,2)
                self.assertEqual(resource_targets.identities(assignment),before)
        self.assertEqual(self.client.post(self.url,self.data(target_scope='object')).status_code,400)

    def test_cross_object_or_company_rejected_by_service_and_ui(self):
        other = ConstructionObject.objects.create(company=self.company,project=self.obj.project,name='Other object')
        section = Section.objects.create(company=self.company,construction_object=other,name='Other section')
        work = ProjectWork.objects.create(company=self.company,section=section,name='Other work',unit='м')
        with self.assertRaises(ValidationError):
            work_resources.save(self.manager,self.version,WorkResourceAssignment(equipment_type=self.equipment,
                start_date=JAN,end_date=JAN),targets=[(self.composite.pk,self.a.pk),(work.pk,None)])
        self.assertFalse(self.version.resource_assignments.exists())
        response = self.client.post(self.url,self.data(targets=[self.tokens()[0],f'work:{work.pk}']))
        self.assertEqual(response.status_code,400)
        foreign = Company.objects.create(name='Foreign target company')
        project = Project.objects.create(company=foreign,name='Foreign project')
        obj = ConstructionObject.objects.create(company=foreign,project=project,name='Foreign object')
        section = Section.objects.create(company=foreign,construction_object=obj,name='Foreign section')
        work = ProjectWork.objects.create(company=foreign,section=section,name='Foreign work',unit='м')
        with self.assertRaises(ValidationError):resource_targets.normalize(self.version,[(work.pk,None)])
        self.assertEqual(self.client.post(self.url,self.data(targets=[self.tokens()[0],f'work:{work.pk}'])).status_code,400)

    def test_secondary_links_change_fingerprints_and_preserve_single_legacy_hashes(self):
        assignment = self.make(targets=self.tokens()[:1])
        before = preparation.resource_signature(self.version)
        estimator_before = signature(self.version)
        from apps.works.merge_planning import input_fingerprint
        merge_before = input_fingerprint(self.version)
        def old_values(owner,fields=None):return list(owner.resource_assignments.order_by('pk').values())
        with patch('apps.planning.resource_targets.assignment_values',side_effect=old_values):
            self.assertEqual(preparation.resource_signature(self.version),before)
            self.assertEqual(signature(self.version),estimator_before)
            self.assertEqual(input_fingerprint(self.version),merge_before)
        preparation.ready(self.manager,self.version)
        response = self.client.post(self.url,self.data(assignment_id=assignment.pk))
        self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(response.json()['workflow']['resources_ready'])
        self.assertNotEqual(preparation.resource_signature(self.version),before)
        self.assertNotEqual(signature(self.version),estimator_before)
        self.assertNotEqual(input_fingerprint(self.version),merge_before)

    def test_copy_to_smg_revision_and_overlay_preserve_links_once(self):
        self.make()
        approved = fixtures.fixtures.WorkspaceTests.approve(self,self.version)
        frozen = deepcopy(approved.snapshot)
        schedule = create(self.planner,self.obj,JAN,approved,copy_from_gpr=True)
        self.prepare_owner(schedule)
        copied = schedule.resource_assignments.get()
        self.assertEqual(len(resource_targets.identities(copied)),2)
        self.assertEqual(sum(Decimal(r['planned_count']) for r in schedule.entries.get(kind='equipment').rows),4)
        self.assertEqual(len(schedule.entries.get(kind='equipment').metadata['work_targets']),2)
        copied.count = 3
        work_resources.save(self.manager,schedule,copied)
        schedule = activate(self.planner,schedule)
        draft = revise(self.planner,schedule)
        self.assertEqual(len(resource_targets.identities(draft.resource_assignments.get())),2)
        snapshot = overlay_schedules({'works':[],'resources':{}},[draft],JAN,date(2026,1,31))
        self.assertEqual(sum(Decimal(r['planned_count']) for r in snapshot['resources']['equipment']),6)
        approved.refresh_from_db();self.assertEqual(approved.snapshot,frozen)

    def test_smg_form_and_closed_days_preserve_target_history(self):
        schedule = create(self.planner,self.obj,JAN)
        self.prepare_owner(schedule)
        url = reverse('planning:work_resources_smg',args=[schedule.pk])
        response = self.client.post(url,self.data())
        self.assertEqual(response.status_code,302,str(response.context['form'].errors) if response.context else '')
        assignment = schedule.resource_assignments.get()
        self.assertEqual(len(resource_targets.identities(assignment)),2)
        before = deepcopy(schedule.entries.get(kind='equipment').rows)
        schedule.locked_before = date(2026,1,2);schedule.save(update_fields=['locked_before'])
        # The normal closed-days form disables association changes and retains all links.
        edit = reverse('planning:work_resource_edit_smg',args=[schedule.pk,assignment.pk])
        response = self.client.post(edit,self.data(targets=[],target_scope='object',count=3))
        self.assertEqual(response.status_code,302,str(response.context['form'].errors) if response.context else '')
        assignment.refresh_from_db();self.assertEqual(len(resource_targets.identities(assignment)),2)
        rows = schedule.entries.get(kind='equipment').rows
        self.assertEqual(rows[0],before[0]);self.assertEqual(Decimal(rows[1]['planned_count']),3)
        with self.assertRaises(ValidationError):work_resources.save(self.manager,schedule,assignment,targets=[(self.composite.pk,self.b.pk)])

    def test_other_manager_cannot_edit_smg_assignments(self):
        from apps.accounts.models import User
        schedule = create(self.planner,self.obj,JAN)
        self.prepare_owner(schedule)
        url = reverse('planning:work_resources_smg',args=[schedule.pk])
        self.assertEqual(self.client.post(url,self.data()).status_code,302)
        assignment = schedule.resource_assignments.get()
        other = User.objects.create_user(username='not-resource-owner',company=self.company,role=self.manager.role)
        self.client.force_login(other)
        self.assertEqual(self.client.post(url,self.data()).status_code,403)
        edit = reverse('planning:work_resource_edit_smg',args=[schedule.pk,assignment.pk])
        self.assertEqual(self.client.post(edit,self.data(count=7)).status_code,403)
        assignment.refresh_from_db();self.assertEqual(assignment.count,2)
        from apps.accounts.models import Role
        other.role = Role.objects.get(code='OBSERVER');other.save()
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(edit,self.data()).status_code,403)

    def test_shared_secondary_work_is_protected_from_deletion(self):
        assignment = self.make()
        secondary = assignment.extra_targets.get()
        with self.assertRaises(ProtectedError):self.b.delete()
        self.assertEqual(secondary.work_item_id,self.b.pk)
        work_resources.remove(self.manager,self.version,assignment)
        self.assertFalse(WorkResourceTarget.objects.exists())

    def test_previewed_purge_discovers_a_plan_through_secondary_work(self):
        from apps.accounts.models import User, Role
        from apps.works.purge import WorkPurge
        from django.db import transaction
        extra = ProjectWork.objects.create(company=self.company,section=self.composite.section,
            name='Secondary standalone work',unit='м')
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=JAN,work=extra,quantity=1)
        assignment = self.make(targets=[self.tokens()[0],f'work:{extra.pk}'])
        self.assertEqual(assignment.work_id,self.composite.pk)
        WorkMonthAllocation.objects.filter(version=self.version,work=extra).delete()
        admin = User.objects.create_user(username='shared-purge-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        preview = WorkPurge(admin,[extra.pk])
        self.assertIn(self.version.pk,preview.graph.ids['planning.GlobalPlanVersion'])
        with transaction.atomic():preview.perform()
        self.assertFalse(ProjectWork.objects.filter(pk=extra.pk).exists())
        self.assertFalse(WorkResourceTarget.objects.exists())
        self.assertTrue(ProjectWork.objects.filter(pk=self.composite.pk).exists())

    def test_audit_failure_rolls_back_targets_and_assignment(self):
        assignment = self.make(targets=self.tokens()[:1])
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=RuntimeError('audit')):
            with self.assertRaises(RuntimeError):
                self.client.post(self.url,self.data(assignment_id=assignment.pk,count=3))
        assignment.refresh_from_db();self.assertEqual(assignment.count,2)
        self.assertFalse(assignment.extra_targets.exists())
