from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from . import test_work_resources as fixtures, work_resources, resource_targets, resource_editor
from .resource_graph import target_token
from .models import WorkMonthAllocation, WorkResourceAssignment
from .independent_smg import create
from .workspace_services import build_workspace_snapshot


class ResourceGraphTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,month=fixtures.JAN,work=self.composite,quantity=1)
        self.client.force_login(self.manager)
        self.a_token=target_token(self.composite.pk,self.a.pk)
        self.b_token=target_token(self.composite.pk,self.b.pk)
        self.row=fixtures.WorkResourceTests.assignment(self,self.version)
        self.url=reverse('planning:work_resources_links_gpr',args=[self.version.pk])

    def post(self,**changes):
        values={'assignment_id':self.row.pk,'revision':self.row.updated_at.isoformat(),
            'targets':[self.a_token,self.b_token]}
        return self.client.post(self.url,{**values,**changes},content_type='application/json')

    def test_month_graph_permissions_and_read_only_approved_plan(self):
        url=reverse('planning:work_resources_graph_gpr',args=[self.version.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Связи работ и ресурсов')
        self.assertTrue(response.context['can_prepare_resources'])
        self.assertEqual(len(response.context['resource_graph_data']['nodes']),2)
        self.assertEqual(self.client.get(url,{'month':'2027-01'}).status_code,404)
        type(self.version).objects.filter(pk=self.version.pk).update(status='APPROVED');self.version.refresh_from_db()
        response=self.client.get(url)
        self.assertFalse(response.context['can_prepare_resources'])
        self.assertNotContains(response,'data-graph-add=')
        self.assertEqual(self.post().status_code,400)
        self.assertEqual(len(resource_targets.identities(self.row)),1)

    def test_links_keep_resource_parameters_and_crew_counted_once(self):
        crew=self.row.crew_snapshot
        response=self.post()
        self.assertEqual(response.status_code,200,response.content)
        self.row.refresh_from_db()
        self.assertEqual(self.row.count,2);self.assertEqual(self.row.crew_snapshot,crew)
        self.assertEqual(self.row.start_date,fixtures.JAN)
        self.assertEqual(self.row.end_date,date(2026,1,2))
        snapshot=build_workspace_snapshot(self.version)
        machines=snapshot['resources']['equipment']
        self.assertEqual(sum(Decimal(r['planned_count']) for r in machines),4)
        self.assertEqual([len(r['work_targets']) for r in machines],[2,2])
        self.assertEqual(self.version.resource_assignments.count(),1)
        self.assertNotIn('link_periods',resource_editor.serialize(self.row))

    def test_invalid_links_extra_parameters_and_stale_revision_are_atomic(self):
        for values in [{'targets':['work:999999']},{'targets':[self.a_token,self.a_token]},
                       {'link_periods':{}},{'hours':8}]:
            response=self.post(**values)
            self.assertEqual(response.status_code,400,response.content)
            self.row.refresh_from_db();self.assertEqual(len(resource_targets.identities(self.row)),1)
        response=self.post();self.assertEqual(response.status_code,200)
        self.assertEqual(self.post(targets=[]).status_code,409)
        self.row.refresh_from_db();self.assertEqual(len(resource_targets.identities(self.row)),2)

    def test_move_to_object_and_back_uses_one_assignment(self):
        response=self.post(targets=[])
        self.assertEqual(response.status_code,200,response.content)
        self.row.refresh_from_db();self.assertIsNone(self.row.work_id)
        self.assertEqual(self.row.count,2)
        response=self.post();self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.version.resource_assignments.count(),1)

    def test_fuel_is_object_only_in_service_and_graph(self):
        fuel=WorkResourceAssignment(fuel_type='DIESEL',daily_liters=10,start_date=fixtures.JAN,end_date=date(2026,1,2))
        fuel=work_resources.save(self.manager,self.version,fuel,targets=[])
        with self.assertRaises(ValidationError):work_resources.save(self.manager,self.version,fuel,targets=[(self.composite.pk,self.a.pk)])
        self.row=fuel
        self.assertEqual(self.post().status_code,400)
        fuel.refresh_from_db();self.assertIsNone(fuel.work_id)

    def test_copy_keeps_shared_links_on_remaining_days(self):
        response=self.post()
        self.assertEqual(response.status_code,200,response.content)
        schedule=create(self.planner,self.obj,fixtures.JAN)
        work_resources.copy_assignments(self.version,schedule,start_on=date(2026,1,2))
        row=schedule.resource_assignments.get()
        self.assertEqual(resource_targets.identities(row),[(self.composite.pk,self.a.pk),(self.composite.pk,self.b.pk)])
        self.assertEqual(row.start_date,date(2026,1,2))
        self.assertEqual(row.count,2)

    def test_closed_smg_days_prevent_rebinding_and_graph_supports_smg(self):
        schedule=create(self.planner,self.obj,fixtures.JAN);self.prepare_owner(schedule)
        work_resources.copy_assignments(self.version,schedule)
        row=schedule.resource_assignments.get()
        schedule.locked_before=date(2026,1,2);schedule.save()
        url=reverse('planning:work_resources_graph_smg',args=[schedule.pk])
        self.assertEqual(self.client.get(url).status_code,200)
        self.url=reverse('planning:work_resources_links_smg',args=[schedule.pk]);self.row=row
        self.assertEqual(self.post().status_code,400)
        row.refresh_from_db();self.assertEqual(len(resource_targets.identities(row)),1)

    def test_audit_error_rolls_back_connections(self):
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):self.post()
        self.row.refresh_from_db();self.assertEqual(len(resource_targets.identities(self.row)),1)

    def test_migration_preserves_approved_fuel_and_moves_only_drafts(self):
        import importlib
        migration=importlib.import_module('apps.planning.migrations.0045_object_fuel_assignments')
        fuel=work_resources.save(self.manager,self.version,WorkResourceAssignment(fuel_type='DIESEL',daily_liters=10,start_date=fixtures.JAN,end_date=date(2026,1,2)),targets=[])
        WorkResourceAssignment.objects.filter(pk=fuel.pk).update(work_id=self.composite.pk,work_item_id=self.a.pk)
        migration.fuel_to_object(apps,connection.schema_editor())
        fuel.refresh_from_db();self.assertIsNone(fuel.work_id);self.assertEqual(fuel.daily_liters,10)
        WorkResourceAssignment.objects.filter(pk=fuel.pk).update(work_id=self.composite.pk,work_item_id=self.a.pk)
        type(self.version).objects.filter(pk=self.version.pk).update(status='APPROVED');self.version.refresh_from_db()
        migration.fuel_to_object(apps,connection.schema_editor())
        fuel.refresh_from_db();self.assertEqual(fuel.work_item_id,self.a.pk)
