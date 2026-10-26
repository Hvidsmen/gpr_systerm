from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from apps.resources.models import EquipmentCrewPosition
from . import test_work_resources as fixtures,work_resources,resource_targets
from .models import WorkMonthAllocation
from .workspace_services import build_workspace_snapshot
from .independent_smg import create


class GraphCrewTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner

    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.client.force_login(self.manager)
        WorkMonthAllocation.objects.create(company=self.company,version=self.version,work=self.composite,month=fixtures.JAN,quantity=1)
        self.machine=fixtures.WorkResourceTests.assignment(self,self.version,include_crew=False)
        self.url=reverse('planning:work_resources_crew_gpr',args=[self.version.pk])

    def payload(self,**changes):
        response=self.client.get(self.url,{'assignment_id':self.machine.pk})
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        return {'assignment_id':self.machine.pk,'revision':data['revision'],'catalogue_revision':data['catalogue_revision'],
                'crew':[{'position_id':self.helper.pk,'count':1,'is_replacement':False}],**changes}

    def post(self,payload=None):return self.client.post(self.url,payload or self.payload(),content_type='application/json')

    def standalone(self,count=2,**values):
        return fixtures.WorkResourceTests.assignment(self,self.version,equipment_type=None,position=self.helper,work=None,work_item=None,count=count,**values)

    def test_saves_catalogue_and_current_crew_retaining_shared_work_links(self):
        work_resources.save(self.manager,self.version,self.machine,targets=[(self.composite.pk,self.a.pk),(self.composite.pk,self.b.pk)])
        response=self.post();self.assertEqual(response.status_code,200,response.content)
        self.machine.refresh_from_db();self.assertTrue(self.machine.include_crew)
        self.assertEqual([m['position_id'] for m in self.machine.crew_snapshot],[self.helper.pk])
        self.assertEqual(len(resource_targets.identities(self.machine)),2)
        self.assertEqual(list(self.equipment.crew_positions.values_list('position_id','count')),[(self.helper.pk,1)])
        future=fixtures.WorkResourceTests.assignment(self,self.version)
        self.assertEqual(future.crew_snapshot,self.machine.crew_snapshot)

    def test_moves_existing_people_into_crew_without_double_count(self):
        people=self.standalone()
        captured_rate=people.crew_snapshot[0]['hourly_rate']
        self.helper.default_hourly_rate=900;self.helper.save()
        before=sum(Decimal(r['planned_count']) for r in build_workspace_snapshot(self.version)['resources']['labor'])
        payload=self.payload();payload['crew'][0].update(source_assignment_id=people.pk,source_revision=people.updated_at.isoformat())
        response=self.post(payload);self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['removed'],[people.pk])
        self.assertFalse(self.version.resource_assignments.filter(pk=people.pk).exists())
        after=sum(Decimal(r['planned_count']) for r in build_workspace_snapshot(self.version)['resources']['labor'])
        self.assertEqual(before,after)
        self.machine.refresh_from_db();self.assertEqual(self.machine.crew_snapshot[0]['hourly_rate'],captured_rate)

    def test_partial_transfer_keeps_remaining_standalone_people_and_links(self):
        people=self.standalone(count=5)
        work_resources.save(self.manager,self.version,people,targets=[(self.composite.pk,self.b.pk)])
        payload=self.payload();payload['crew'][0].update(source_assignment_id=people.pk,source_revision=people.updated_at.isoformat())
        response=self.post(payload);self.assertEqual(response.status_code,200,response.content)
        people.refresh_from_db();self.assertEqual(people.count,3)
        self.assertEqual(resource_targets.identities(people),[(self.composite.pk,self.b.pk)])

    def test_mismatched_period_or_insufficient_people_does_not_mutate_catalogue(self):
        people=self.standalone(count=1)
        before=list(self.equipment.crew_positions.values_list('position_id','count'))
        payload=self.payload();payload['crew'][0].update(source_assignment_id=people.pk,source_revision=people.updated_at.isoformat())
        self.assertEqual(self.post(payload).status_code,400)
        people.count=5;people.end_date=date(2026,1,3);work_resources.save(self.manager,self.version,people)
        payload['crew'][0]['source_revision']=people.updated_at.isoformat()
        self.assertEqual(self.post(payload).status_code,400)
        self.assertEqual(list(self.equipment.crew_positions.values_list('position_id','count')),before)
        self.machine.refresh_from_db();self.assertFalse(self.machine.include_crew)

    def test_optimistic_revisions_cover_both_catalogue_and_source(self):
        payload=self.payload();EquipmentCrewPosition.objects.filter(pk=self.crew.pk).update(count=4)
        self.assertEqual(self.post(payload).status_code,409)
        people=self.standalone();payload=self.payload();payload['crew'][0].update(source_assignment_id=people.pk,source_revision='old')
        self.assertEqual(self.post(payload).status_code,409)
        payload=self.payload();payload['revision']='old';self.assertEqual(self.post(payload).status_code,409)

    def test_preserves_other_plan_crews_and_captured_rates(self):
        self.machine.include_crew=True;work_resources.save(self.manager,self.version,self.machine)
        other=fixtures.WorkResourceTests.assignment(self,self.version)
        old_snapshot=other.crew_snapshot
        self.position.default_hourly_rate=999;self.position.save()
        payload=self.payload(crew=[{'position_id':self.position.pk,'count':2,'is_replacement':False}])
        response=self.post(payload);self.assertEqual(response.status_code,200,response.content)
        self.machine.refresh_from_db();other.refresh_from_db()
        self.assertEqual(other.crew_snapshot,old_snapshot)
        self.assertEqual(self.machine.crew_snapshot[0]['hourly_rate'],'100.00')

    def test_empty_crew_disables_capture_in_current_assignment(self):
        response=self.post(self.payload(crew=[]));self.assertEqual(response.status_code,200,response.content)
        self.machine.refresh_from_db();self.assertFalse(self.machine.include_crew);self.assertEqual(self.machine.crew_snapshot,[])
        self.assertFalse(self.equipment.crew_positions.exists())

    def test_invalid_rows_are_atomic_and_get_never_exposes_pay(self):
        response=self.client.get(self.url,{'assignment_id':self.machine.pk})
        self.assertNotIn('hourly_rate',response.content.decode())
        row={'position_id':self.helper.pk,'count':1,'is_replacement':False}
        for crew in [[row,row],[{**row,'count':0}],[{**row,'position_id':999999}],[{**row,'rate':100}]]:
            self.assertEqual(self.post(self.payload(crew=crew)).status_code,400)
        self.machine.refresh_from_db();self.assertFalse(self.machine.include_crew)

    def test_approved_plan_and_unassigned_manager_cannot_update_catalogue(self):
        from apps.accounts.models import User,Role
        other_manager=User.objects.create_user(username='other-crew-manager',company=self.company,role=Role.objects.get(code='MANAGER'))
        payload=self.payload();self.client.force_login(other_manager)
        self.assertIn(self.post(payload).status_code,[403,404])
        self.client.force_login(self.manager)
        type(self.version).objects.filter(pk=self.version.pk).update(status='APPROVED')
        self.assertEqual(self.post(payload).status_code,400)
        self.assertEqual(self.equipment.crew_positions.count(),2)

    def test_smg_closed_days_block_crew_changes(self):
        schedule=create(self.planner,self.obj,fixtures.JAN);self.prepare_owner(schedule)
        self.machine=fixtures.WorkResourceTests.assignment(self,schedule,include_crew=False)
        self.url=reverse('planning:work_resources_crew_smg',args=[schedule.pk])
        payload=self.payload();schedule.locked_before=date(2026,1,2);schedule.save()
        self.assertEqual(self.post(payload).status_code,400)
        self.assertEqual(self.equipment.crew_positions.count(),2)

    def test_open_smg_transfer_regenerates_labor_once(self):
        schedule=create(self.planner,self.obj,fixtures.JAN);self.prepare_owner(schedule)
        self.machine=fixtures.WorkResourceTests.assignment(self,schedule,include_crew=False)
        people=fixtures.WorkResourceTests.assignment(self,schedule,equipment_type=None,position=self.helper,count=2,work=None,work_item=None)
        self.url=reverse('planning:work_resources_crew_smg',args=[schedule.pk])
        payload=self.payload();payload['crew'][0].update(source_assignment_id=people.pk,source_revision=people.updated_at.isoformat())
        response=self.post(payload);self.assertEqual(response.status_code,200,response.content)
        labor=schedule.entries.get(kind='labor')
        self.assertEqual(sum(Decimal(r['planned_workers']) for r in labor.rows),8)
        self.assertEqual(sum(Decimal(r['planned_hours']) for r in labor.rows),64)
        self.assertFalse(schedule.resource_assignments.filter(pk=people.pk).exists())

    def test_audit_failure_rolls_back_plan_catalogue_and_transferred_people(self):
        people=self.standalone();payload=self.payload()
        payload['crew'][0].update(source_assignment_id=people.pk,source_revision=people.updated_at.isoformat())
        with patch('apps.audit.models.AuditLog.objects.create',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.post(payload)
        self.assertEqual(self.equipment.crew_positions.count(),2)
        self.machine.refresh_from_db();self.assertFalse(self.machine.include_crew)
        people.refresh_from_db();self.assertEqual(people.count,2)
