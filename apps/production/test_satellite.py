import json, os, time
from pathlib import Path
from uuid import uuid4
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from .test_production_collaboration import ProductionCollaborationTests
from .dispatch_models import EmployeeActivity
from .models import DailyFact
from apps.resources.models import Employee
from apps.works.models import ProjectWork


class SatelliteProfileTests(TestCase):
    setUpTestData=classmethod(ProductionCollaborationTests.setUpTestData.__func__)
    def test_representative_day_profile(self):
        user=self.users['DISPATCHER'];self.client.force_login(user)
        employee_count=int(os.environ.get('SATELLITE_EMPLOYEES',300));people_count=int(os.environ.get('SATELLITE_PEOPLE',120));work_count=int(os.environ.get('SATELLITE_WORKS',40))
        employees=[Employee(company=self.company,last_name=f'Сотрудник {i:05}',first_name='Тест',employee_number=f'SAT-{i}',position=self.employee.position) for i in range(employee_count)]
        Employee.objects.bulk_create(employees)
        EmployeeActivity.objects.bulk_create([EmployeeActivity(company=self.company,construction_object=self.obj,date=self.day,employee=employee,position=employee.position,entry_direction='works',assignment_reviewed=False,reported_by=user) for employee in employees[:people_count]])
        works=[ProjectWork(company=self.company,section=self.work.section,code=f'SAT-{n}',name=f'Работа {n:04}',unit='м') for n in range(work_count)]
        ProjectWork.objects.bulk_create(works)
        DailyFact.objects.bulk_create([DailyFact(company=self.company,date=self.day,project_work=work,actual_quantity=5,reported_by=user) for work in works])
        scope={'actor_id':user.pk,'company_id':self.company.pk,'construction_object':self.obj.pk,'date':str(self.day),'session':str(uuid4()),'reset_epoch':0}
        started=time.perf_counter()
        with CaptureQueriesContext(connection) as queries:page=self.client.get(self.url,{**scope,'section':'works'})
        result={'people':people_count,'employees':employee_count,'works':work_count,'html_bytes':len(page.content),'employee_option_count':page.content.count(b'<option value='),'page_queries':len(queries),'page_seconds':round(time.perf_counter()-started,3)}
        snap=page.context['production_snapshot'];person=EmployeeActivity.objects.first();fact=DailyFact.objects.first()
        baseline=os.environ.get('SATELLITE_PROFILE')=='baseline'
        for name,route,payload in [('live','production_live',{**scope,'production_snapshot':snap}),('link','production_links',{**scope,'activity':person.pk,'fact':fact.pk,'action':'add'})]:
            if not baseline:
                if name=='live':
                    payload.update(protocol='compact-v1',visible_people=list(EmployeeActivity.objects.order_by('pk').values_list('pk',flat=True)[:100]),visible_works=list(DailyFact.objects.order_by('pk').values_list('pk',flat=True)[:100]))
                    payload['visible_claims']=[f'employee:{employee}:{shift}' for employee,shift in EmployeeActivity.objects.order_by('pk').values_list('employee_id','shift')[:100]]
                else:payload={**scope,'protocol':'queued-v1','commands':[{'id':str(uuid4()),'activity':person.pk,'fact':fact.pk,'action':'add'}]}
            started=time.perf_counter()
            with CaptureQueriesContext(connection) as queries:response=self.client.post(reverse('production:'+route),payload,content_type='application/json')
            self.assertEqual(response.status_code,200,response.content[:400]);result[name+'_bytes']=len(response.content);result[name+'_queries']=len(queries);result[name+'_seconds']=round(time.perf_counter()-started,3)
        if not baseline:
            first=self.client.post(reverse('production:production_live'),{**scope,'protocol':'compact-v1','production_snapshot':snap},content_type='application/json').json()
            idle={**scope,'protocol':'compact-v1','snapshot_key':first['snapshot_key'],'digest':first['digest'],'users_digest':first['users_digest'],'claims_digest':first['claims_digest']}
            with CaptureQueriesContext(connection) as queries:reply=self.client.post(reverse('production:production_live'),idle,content_type='application/json')
            result['idle_live_bytes']=len(reply.content);result['idle_live_queries']=len(queries);result['idle_request_bytes']=len(json.dumps(idle).encode())
            compressed=self.client.get(self.url,{**scope,'section':'works'},HTTP_ACCEPT_ENCODING='gzip');result['html_gzip_bytes']=len(compressed.content)
            legacy={**scope,'production_snapshot':snap};old=self.client.post(reverse('production:production_live'),legacy,content_type='application/json').json();legacy['digest']=old['digest'];old_idle=self.client.post(reverse('production:production_live'),legacy,content_type='application/json');result['legacy_idle_live_bytes']=len(old_idle.content);result['legacy_idle_request_bytes']=len(json.dumps(legacy).encode())
            if employee_count==300 and people_count==120 and work_count==40:
                self.assertLess(result['html_gzip_bytes'],150000)
                self.assertLess(result['html_bytes'],2000000);self.assertLess(result['page_queries'],250)
            self.assertLess(result['link_bytes'],2000);self.assertLess(result['idle_live_bytes'],1024)
        result['poll_request_bytes']=len(json.dumps({**scope,'production_snapshot':snap}).encode())
        label=os.environ.get('SATELLITE_PROFILE','current');Path('/tmp/satellite-profile-'+label+'.json').write_text(json.dumps(result,indent=2));print('SATELLITE_PROFILE',json.dumps(result))

from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.core.cache import cache
from .dispatch_models import (ProductionDailyCrew,ProductionDailyCrewMember,
    ProductionDailyCrewWorkLink,ProductionLinkReceipt,EmployeeWorkLink)
from .models import FactSectionDay


class SatelliteProtocolTests(TestCase):
    setUpTestData=classmethod(ProductionCollaborationTests.setUpTestData.__func__)
    setUp=ProductionCollaborationTests.setUp
    payload=ProductionCollaborationTests.payload
    edge=ProductionCollaborationTests.edge
    def command(self,**values):return {'id':str(uuid4()),'activity':self.person.pk,'action':'add','fact':self.fact.pk,**values}
    def send(self,commands,**values):
        return self.client.post(reverse('production:production_links'),{**self.scope,'actor_id':self.user.pk,'company_id':self.company.pk,'protocol':'queued-v1','commands':commands,**values},content_type='application/json')
    def initial_sync(self):
        page=self.client.get(self.url,{**self.scope,'section':'works'})
        signed=page.context['production_snapshot']
        response=self.client.post(reverse('production:production_live'),{**self.scope,'actor_id':self.user.pk,'company_id':self.company.pk,'protocol':'compact-v1','production_snapshot':signed},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content);return response.json(),signed
    def idle(self,first,**extra):
        return self.client.post(reverse('production:production_live'),{**self.scope,'actor_id':self.user.pk,'company_id':self.company.pk,'protocol':'compact-v1','snapshot_key':first['snapshot_key'],'digest':first['digest'],'claims_digest':first['claims_digest'],'users_digest':first['users_digest'],**extra},content_type='application/json')
    def test_batch_compact_ack_and_exactly_once_after_lost_response(self):
        command=self.command();response=self.send([command]);self.assertEqual(response.status_code,200)
        data=response.json();self.assertTrue(data['compact']);self.assertNotIn('report',data);self.assertLess(len(response.content),1500)
        self.person.refresh_from_db();revision=self.person.links_revision
        retry=self.send([command]);self.assertEqual(retry.status_code,200);self.person.refresh_from_db()
        self.assertEqual(self.person.links_revision,revision);self.assertEqual(ProductionLinkReceipt.objects.count(),1);self.assertEqual(self.person.work_links.count(),1)
    def test_retry_does_not_restore_edge_removed_by_colleague(self):
        command=self.command();self.send([command]);self.edge('remove');self.assertFalse(self.person.work_links.exists())
        data=self.send([command]).json();self.assertFalse(self.person.work_links.exists());self.assertEqual(data['people'][0]['targets'],[])
    def test_batches_apply_readiness_once_and_keep_order(self):
        commands=[self.command(),self.command(action='remove'),self.command(fact=self.second_fact.pk)]
        with patch('apps.production.collaboration.automatic_readiness') as ready:response=self.send(commands)
        self.assertEqual(response.status_code,200);self.assertTrue(all(r['ok'] for r in response.json()['results']));ready.assert_called_once()
        self.assertEqual(set(self.person.work_links.values_list('work_id',flat=True)),{self.second_work.pk})
    def test_same_work_remains_linked_to_multiple_people(self):
        employee=Employee.objects.create(company=self.company,last_name='Второй',first_name='Сотрудник',position=self.employee.position)
        other=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=employee,position=employee.position,assignment_reviewed=False,entry_direction='works',reported_by=self.user)
        response=self.send([self.command(),self.command(activity=other.pk)])
        self.assertEqual(response.status_code,200,response.content);self.assertTrue(all(row['ok'] for row in response.json()['results']))
        self.assertTrue(self.person.work_links.filter(work=self.work).exists());self.assertTrue(other.work_links.filter(work=self.work).exists())
        linked={row['id']:row['targets'] for row in response.json()['people']}
        self.assertIn(f'work:{self.work.pk}',linked[self.person.pk]);self.assertIn(f'work:{self.work.pk}',linked[other.pk])
        untouched=Employee.objects.create(company=self.company,last_name='Незатронутый',first_name='Сотрудник',position=self.employee.position)
        EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,employee=untouched,position=untouched.position,assignment_reviewed=False,entry_direction='works',reported_by=self.user)
        response=self.send([self.command(action='remove')]);self.assertEqual({row['id'] for row in response.json()['people']},{self.person.pk})
    def test_queued_crew_link_records_brigade_origin(self):
        crew=ProductionDailyCrew.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=self.person.shift,number=1,reported_by=self.user)
        employee=Employee.objects.create(company=self.company,last_name='Второй',first_name='Участник',position=self.employee.position)
        other=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=self.person.shift,employee=employee,position=employee.position,assignment_reviewed=False,entry_direction='works',reported_by=self.user)
        for activity in [self.person,other]:ProductionDailyCrewMember.objects.create(company=self.company,crew=crew,activity=activity)
        response=self.send([self.command(crew=crew.pk),self.command(activity=other.pk,crew=crew.pk)]);self.assertEqual(response.status_code,200,response.content)
        link=EmployeeWorkLink.objects.get(activity=self.person,work=self.fact.project_work)
        self.assertFalse(link.assigned_individually);self.assertEqual(link.crew_source_id,crew.pk)
        self.assertTrue(ProductionDailyCrewWorkLink.objects.filter(crew=crew,fact=self.fact).exists())
        response=self.send([self.command(action='remove',crew=crew.pk),self.command(activity=other.pk,action='remove',crew=crew.pk)]);self.assertEqual(response.status_code,200,response.content)
        self.assertFalse(ProductionDailyCrewWorkLink.objects.filter(crew=crew,fact=self.fact).exists());self.assertFalse(EmployeeWorkLink.objects.filter(activity__in=[self.person,other],work=self.fact.project_work).exists())
    def test_whole_crew_command_is_one_idempotent_atomic_change(self):
        crew=ProductionDailyCrew.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=self.person.shift,number=1,reported_by=self.user)
        employee=Employee.objects.create(company=self.company,last_name='Второй',first_name='Участник',position=self.employee.position)
        other=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=self.person.shift,employee=employee,position=employee.position,assignment_reviewed=False,entry_direction='works',reported_by=self.user)
        for activity in [self.person,other]:ProductionDailyCrewMember.objects.create(company=self.company,crew=crew,activity=activity)
        command={'id':str(uuid4()),'action':'crew_add','crew':crew.pk,'fact':self.fact.pk}
        response=self.send([command]);self.assertEqual(response.status_code,200,response.content);self.assertTrue(response.json()['results'][0]['ok'])
        self.assertEqual(EmployeeWorkLink.objects.filter(crew_source=crew).count(),2);self.assertEqual({row['id'] for row in response.json()['people']},{self.person.pk,other.pk})
        self.assertEqual(ProductionLinkReceipt.objects.count(),1);self.assertEqual(self.send([command]).status_code,200);self.assertEqual(EmployeeWorkLink.objects.filter(crew_source=crew).count(),2)
        remove={'id':str(uuid4()),'action':'crew_remove','crew':crew.pk,'fact':self.fact.pk}
        self.assertTrue(self.send([remove]).json()['results'][0]['ok']);self.assertFalse(EmployeeWorkLink.objects.filter(crew_source=crew).exists())
    def test_whole_crew_command_rolls_back_every_member_on_error(self):
        crew=ProductionDailyCrew.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=self.person.shift,number=1,reported_by=self.user)
        employee=Employee.objects.create(company=self.company,last_name='Второй',first_name='Участник',position=self.employee.position)
        other=EmployeeActivity.objects.create(company=self.company,construction_object=self.obj,date=self.day,shift=self.person.shift,employee=employee,position=employee.position,assignment_reviewed=False,entry_direction='works',reported_by=self.user)
        for activity in [self.person,other]:ProductionDailyCrewMember.objects.create(company=self.company,crew=crew,activity=activity)
        with patch('apps.production.crew_links._ensure_link',side_effect=[None,ValidationError('Сбой')]):
            data=self.send([{'id':str(uuid4()),'action':'crew_add','crew':crew.pk,'fact':self.fact.pk}]).json()
        self.assertFalse(data['results'][0]['ok']);self.assertFalse(ProductionDailyCrewWorkLink.objects.filter(crew=crew).exists());self.assertFalse(EmployeeWorkLink.objects.filter(crew_source=crew).exists())
    def test_zero_work_rejected_without_restore_and_other_operation_succeeds(self):
        self.fact.actual_quantity=0;self.fact.save()
        data=self.send([self.command(),self.command(fact=self.second_fact.pk)]).json()
        self.assertFalse(data['results'][0]['ok']);self.assertTrue(data['results'][1]['ok']);self.assertEqual(self.person.work_links.count(),1)
    def test_failed_delta_is_atomic(self):
        data=self.send([self.command(action='delta',edges=[{'action':'add','fact':self.fact.pk},{'action':'add','fact':999999}])]).json()
        self.assertFalse(data['results'][0]['ok']);self.assertFalse(self.person.work_links.exists())
    def test_receipt_collision_validated_before_any_batch_mutations(self):
        command=self.command();self.send([command]);self.person.refresh_from_db();before=self.person.links_revision
        response=self.send([self.command(fact=self.second_fact.pk),{**command,'action':'remove'}])
        self.assertEqual(response.status_code,409);self.person.refresh_from_db();self.assertEqual(self.person.links_revision,before);self.assertEqual(self.person.work_links.count(),1)
    def test_mode_requires_current_revision_and_receipt_replay_works(self):
        command=self.command(action='mode',mode='general',revision=0);data=self.send([command]).json();self.assertTrue(data['results'][0]['ok'])
        self.assertTrue(self.send([command]).json()['results'][0]['ok'])
        self.assertFalse(self.send([self.command(action='mode',mode='pending',revision=0)]).json()['results'][0]['ok'])
    def test_edit_window_checked_but_successful_receipt_can_be_acknowledged_later(self):
        command=self.command();self.send([command])
        with patch('apps.production.satellite_links.require_fact_day',side_effect=ValidationError('День закрыт')):
            self.assertTrue(self.send([command]).json()['results'][0]['ok'])
            self.assertFalse(self.send([self.command(action='remove')]).json()['results'][0]['ok'])
        self.assertTrue(self.person.work_links.exists())
    def test_reset_generation_stops_old_queue(self):
        response=self.send([self.command()],reset_epoch=999);self.assertEqual(response.status_code,409);self.assertTrue(response.json()['reset_required']);self.assertFalse(ProductionLinkReceipt.objects.exists())
    def test_envelope_limits_and_duplicates_have_no_partial_effect(self):
        command=self.command()
        for commands in [[],[command,command],[self.command() for _ in range(41)],[self.command(activity=-1)]]:
            self.assertEqual(self.send(commands).status_code,400)
        self.assertFalse(ProductionLinkReceipt.objects.exists());self.assertFalse(self.person.work_links.exists())
    def test_other_object_and_company_cannot_be_changed(self):
        from apps.accounts.models import User,Role,Company
        foreign=Company.objects.create(name='Чужая');employee=Employee.objects.create(company=foreign,last_name='Чужой')
        self.assertFalse(self.send([self.command(activity=999999)]).json()['results'][0]['ok'])
        self.client.force_login(self.users['PERSONNEL_DISPATCHER']);self.assertEqual(self.send([self.command()]).status_code,403)
    def test_idle_sync_omits_report_snapshot_presence_and_claims(self):
        first,_=self.initial_sync()
        with patch('apps.production.collaboration.report_data',side_effect=AssertionError('idle must not build report')):response=self.idle(first)
        self.assertEqual(response.status_code,200);data=response.json();self.assertLess(len(response.content),1024)
        for name in ('report','rows','production_snapshot','users','claims'):self.assertNotIn(name,data)
    def test_snapshot_cache_miss_requests_baseline_without_losing_form(self):
        first,_=self.initial_sync();cache.clear();data=self.idle(first).json();self.assertTrue(data['need_snapshot']);self.assertNotIn('rows',data)
    def test_changed_data_gets_new_signed_baseline_and_canonical_links(self):
        first,_=self.initial_sync();self.send([self.command()]);data=self.idle(first).json()
        self.assertIn('production_snapshot',data);self.assertEqual(data['report']['people'][0]['targets'],[f'work:{self.work.pk}'])
    def test_baseline_cache_cannot_cross_accounts(self):
        first,signed=self.initial_sync();self.client.force_login(self.second)
        response=self.client.post(reverse('production:production_live'),{**self.scope,'actor_id':self.second.pk,'company_id':self.company.pk,'session':str(uuid4()),'protocol':'compact-v1','production_snapshot':signed},content_type='application/json')
        self.assertEqual(response.status_code,400)
    def test_json_and_html_compression_and_local_assets(self):
        import gzip
        page=self.client.get(self.url,{**self.scope,'section':'works'},HTTP_ACCEPT_ENCODING='gzip')
        self.assertEqual(page['Content-Encoding'],'gzip');body=gzip.decompress(page.content)
        self.assertIn(b'/static/vendor/bootstrap-5.3.0/bootstrap.min.css',body);self.assertNotIn(b'https://cdn.jsdelivr.net',body)


    def test_ack_digest_only_when_client_was_current(self):
        first,_=self.initial_sync()
        data=self.send([self.command()],data_digest=first['digest']).json();self.assertIn('digest',data)
        self.fact.actual_quantity=4;self.fact.save()
        data=self.send([self.command(action='remove')],data_digest=first['digest']).json();self.assertNotIn('digest',data)
    def test_idle_stamp_detects_bulk_work_field_updates(self):
        first,_=self.initial_sync();DailyFact.objects.filter(pk=self.fact.pk).update(comment='Изменён комментарий')
        self.assertIn('report',self.idle(first).json())
    def test_bulk_employee_reservation_is_single_atomic_request(self):
        scope={**self.scope,'action':'take_many','keys':[f'employee:{self.employee.pk}:1',f'employee:{self.cook.pk}:1']}
        response=self.client.post(reverse('production:work_claims'),scope,content_type='application/json')
        self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()['claims']),2)
        from .dispatch_models import EmployeeFactClaim
        self.assertEqual(EmployeeFactClaim.objects.count(),2)
        response=self.client.post(reverse('production:work_claims'),{**scope,'action':'release_many'},content_type='application/json')
        self.assertEqual(response.status_code,200);self.assertEqual(EmployeeFactClaim.objects.count(),0)
    def test_bulk_reservation_conflict_rolls_back_earlier_claims(self):
        from .dispatch_models import EmployeeFactClaim
        self.client.force_login(self.second)
        self.client.post(reverse('production:work_claims'),{**self.scope,'session':str(uuid4()),'action':'take','key':f'employee:{self.cook.pk}:1'},content_type='application/json')
        self.client.force_login(self.user)
        response=self.client.post(reverse('production:work_claims'),{**self.scope,'action':'take_many','keys':[f'employee:{self.employee.pk}:1',f'employee:{self.cook.pk}:1']},content_type='application/json')
        self.assertEqual(response.status_code,409);self.assertFalse(EmployeeFactClaim.objects.filter(employee=self.employee).exists())
    def test_collectstatic_produces_compressed_local_vendor_assets(self):
        from tempfile import TemporaryDirectory
        from django.test import override_settings
        from django.core.management import call_command
        with TemporaryDirectory() as root,override_settings(STATIC_ROOT=root):
            call_command('collectstatic',interactive=False,verbosity=0)
            self.assertTrue(Path(root,'vendor/bootstrap-5.3.0/bootstrap.min.css.gz').exists())
            self.assertTrue(Path(root,'vendor/bootstrap-5.3.0/bootstrap.bundle.min.js.gz').exists())


    def test_legacy_primary_assignment_is_not_lost_when_adding_another_work(self):
        self.person.work=self.work;self.person.assignment_reviewed=True;self.person.save()
        data=self.send([self.command(fact=self.second_fact.pk)]).json()
        self.assertTrue(data['results'][0]['ok']);self.assertEqual(set(self.person.work_links.values_list('work_id',flat=True)),{self.work.pk,self.second_work.pk})

    def test_cached_queue_cannot_apply_after_browser_account_switch(self):
        command=self.command();self.client.force_login(self.second)
        response=self.send([command]);self.assertEqual(response.status_code,403);self.assertTrue(response.json()['auth_changed'])
        self.assertFalse(ProductionLinkReceipt.objects.exists());self.assertFalse(self.person.work_links.exists())

# Imported fixture classes are helpers, not additional tests in this module.
del ProductionCollaborationTests
