"""Both production access lists permit edits; only main assignments create daily tasks."""
from datetime import timedelta
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User, Role, Company, AccessGroup
from apps.accounts.forms import CompanyUserAccessForm
from apps.accounts.access_groups import map_directory_group
from apps.projects.models import ConstructionObject, Project, Section
from apps.works.models import ProjectWork
from apps.planning.models import WorkTask, DailyTaskCursor
from apps.planning.task_services import generate_daily
from core.permissions import scope_queryset
from . import test_dispatch as fixtures
from .models import DailyFact
from .dispatch_models import FactSectionDay, ProductionEntryDay, WorkFactClaim, EmployeeFactClaim, EmployeeActivity, EmployeeWorkLink, ProductionLinkReceipt


class ProductionObjectAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        fixtures.DispatcherTests.setUpTestData.__func__(cls)
        cls.hidden=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='Hidden object')
        cls.foreign_company=Company.objects.create(name='Foreign company')
        cls.foreign=ConstructionObject.objects.create(company=cls.foreign_company,project=Project.objects.create(company=cls.foreign_company,name='Foreign project'),name='Foreign object')
        section=Section.objects.create(company=cls.company,construction_object=cls.other,name='Other section')
        cls.other_work=ProjectWork.objects.create(company=cls.company,section=section,name='Additional object work',unit='m')
        cls.fact=DailyFact.objects.create(company=cls.company,project_work=cls.other_work,date=cls.day,actual_quantity=7,reported_by=cls.users['ADMIN'])
        cls.person=EmployeeActivity.objects.create(company=cls.company,construction_object=cls.other,date=cls.day,employee=cls.employee,position=cls.employee.position,entry_direction='works',reported_by=cls.users['ADMIN'])

    def setUp(self):
        self.user=self.users['DISPATCHER'];self.user.production_additional_objects.add(self.other)
        self.client.force_login(self.user)
        self.query={'construction_object':self.other.pk,'date':str(self.day),'section':'works'}
        self.session=str(uuid4());self.scope={**self.query,'session':self.session}

    def payload(self):
        page=self.client.get(self.url,self.query);self.assertEqual(page.status_code,200)
        data={**self.query,'revision':str(page.context['status'].revision),'labor_revision':str(page.context['labor_revision']),'production_snapshot':page.context['production_snapshot'],'action':'save'}
        for group in page.context['groups']:
            for field in group['formset'].management_form:data[field.html_name]=field.value()
            for row in group['formset']:
                for field in row:
                    if field.name=='DELETE':continue
                    value=field.value();data[field.html_name]=value if isinstance(value,list) else str(value) if value is not None and value is not False else ''
        return data

    def test_production_union_without_expanding_nonproduction_scope(self):
        self.assertSetEqual(set(scope_queryset(ConstructionObject.objects.all(),self.user,production_scope=True)),{self.obj,self.other})
        self.assertSetEqual(set(scope_queryset(ConstructionObject.objects.all(),self.user)),{self.obj})
        self.user.production_additional_objects.add(self.obj)
        self.assertEqual(scope_queryset(ConstructionObject.objects.all(),self.user,production_scope=True).count(),2)

    def test_additional_day_is_editable_and_has_existing_facts_and_collaboration_scripts(self):
        page=self.client.get(self.url,self.query)
        self.assertTrue(page.context['editable']);self.assertTrue(page.context['own_entry'])
        self.assertContains(page,'data-readonly="0"');self.assertContains(page,'production/production_autosave.js');self.assertContains(page,'production/links_outbox.js')
        works=next(g['formset'] for g in page.context['groups'] if g['prefix']=='works')
        self.assertIn(self.fact.pk,[row.instance.pk for row in works])
        self.assertFalse(WorkTask.objects.filter(assigned_to=self.user,construction_object=self.other,kind='FACT').exists())

    def test_actual_fact_can_be_changed_on_additional_object(self):
        data=self.payload();prefix=next(k[:-len('project_work')] for k,v in data.items() if k.endswith('-project_work') and str(v)==str(self.other_work.pk))
        data.update({prefix+'actual_quantity':'12','collaborative':'1','autosave':'1'})
        response=self.client.post(self.url,data);self.assertEqual(response.status_code,200,response.content)
        self.fact.refresh_from_db();self.assertEqual(self.fact.actual_quantity,12)
        self.assertEqual(self.fact.reported_by_id,self.users['ADMIN'].pk)
        self.assertTrue(ProductionEntryDay.objects.filter(construction_object=self.other,dispatcher=self.user).exists())
        generate_daily(self.user,today=self.day+timedelta(days=1))
        self.assertFalse(WorkTask.objects.filter(assigned_to=self.user,construction_object=self.other,kind='FACT').exists())

    def test_claims_presence_and_both_link_protocols_allow_additional_object(self):
        response=self.client.post(reverse('production:work_claims'),{**self.scope,'action':'take','key':f'work:{self.other_work.pk}'},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        self.assertTrue(WorkFactClaim.objects.filter(dispatcher=self.user,construction_object=self.other).exists())
        response=self.client.post(reverse('production:production_live'),self.scope,content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        response=self.client.post(reverse('production:production_links'),{**self.scope,'activity':self.person.pk,'fact':self.fact.pk,'action':'add'},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content);self.assertTrue(self.person.work_links.exists())
        response=self.client.post(reverse('production:production_links'),{**self.scope,'protocol':'queued-v1','actor_id':self.user.pk,'company_id':self.company.pk,'commands':[{'id':str(uuid4()),'activity':self.person.pk,'action':'remove','fact':self.fact.pk}]},content_type='application/json')
        self.assertEqual(response.status_code,200,response.content);self.assertTrue(response.json()['results'][0]['ok']);self.assertFalse(self.person.work_links.exists())

    def test_tasks_only_for_main_objects_and_removal_preserves_additional_editing(self):
        generate_daily(self.user,today=self.day+timedelta(days=1))
        tasks=WorkTask.objects.filter(assigned_to=self.user,kind='FACT')
        self.assertSetEqual(set(tasks.values_list('construction_object_id',flat=True)),{self.obj.pk})
        self.assertFalse(DailyTaskCursor.objects.filter(user=self.user,construction_object=self.other).exists())
        self.user.production_additional_objects.add(self.obj);self.user.assigned_objects.set([self.other])
        generate_daily(self.user,today=self.day+timedelta(days=1))
        self.assertEqual(tasks.get(construction_object=self.obj).status,'SUPERSEDED');self.assertEqual(tasks.get(construction_object=self.other).status,'OPEN')
        for obj in [self.obj,self.other]:self.assertTrue(self.client.get(self.url,{**self.query,'construction_object':obj.pk}).context['editable'])

    def test_no_main_objects_still_can_edit_without_daily_tasks(self):
        self.user.assigned_objects.clear();self.assertEqual(generate_daily(self.user,today=self.day+timedelta(days=1)),0)
        self.assertTrue(self.client.get(self.url,self.query).context['editable'])
        self.assertFalse(WorkTask.objects.filter(assigned_to=self.user,kind='FACT').exists())

    def test_hidden_and_foreign_objects_stay_blocked_for_reads_and_writes(self):
        for obj in [self.hidden,self.foreign]:
            query={**self.query,'construction_object':obj.pk}
            self.assertNotIn('object',self.client.get(self.url,query).context)
            for route in ['production:fact_day_workspace','production:work_claims','production:production_links','production:production_copy_previous','production:actuation_reason_propose','production:actuation_documents']:
                response=self.client.post(reverse(route),{**query,'session':self.session,'activity':self.person.pk,'fact':self.fact.pk,'action':'add'})
                self.assertIn(response.status_code,{400,403,404,409},route)
        self.fact.refresh_from_db();self.assertEqual(self.fact.actual_quantity,7)
        self.assertFalse(EmployeeWorkLink.objects.exists());self.assertFalse(ProductionLinkReceipt.objects.exists())
        self.user.production_additional_objects.clear()
        self.assertNotIn('object',self.client.get(self.url,self.query).context)
        self.assertIn(self.client.post(self.url,self.query).status_code,{400,403})

    def test_reports_and_exports_allow_additional_object(self):
        for route in ['production:production_report_download','production:smg_period_detail','production:smg_period_export','production:equipment_report']:
            response=self.client.get(reverse(route),self.query);self.assertEqual(response.status_code,200)
            if response.streaming:response.close()

    def test_other_roles_do_not_gain_rights_from_additional_production_objects(self):
        for code in ['PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER']:
            user=self.users[code];user.production_additional_objects.add(self.other)
            self.assertFalse(scope_queryset(ConstructionObject.objects.all(),user,production_scope=True).filter(pk=self.other.pk).exists())

    def test_all_direction_dispatcher_can_edit_without_automatic_task(self):
        user=User.objects.create_user(username='all-object-scope',company=self.company,role=Role.objects.get(code='ALL_FACT_DISPATCHER'))
        user.assigned_objects.add(self.obj);user.production_additional_objects.add(self.other);self.client.force_login(user)
        for section in ['works','labor','mechanization']:
            page=self.client.get(self.url,{**self.query,'section':section});self.assertEqual(page.status_code,200);self.assertTrue(page.context['editable'])
        generate_daily(user,today=self.day+timedelta(days=1))
        self.assertFalse(WorkTask.objects.filter(assigned_to=user,construction_object=self.other,kind='FACT').exists())

    def test_company_guards_and_group_inheritance(self):
        with self.assertRaises(ValidationError),transaction.atomic():self.user.production_additional_objects.add(self.foreign)
        outsider=User.objects.create_user(username='outsider-object-scope',company=self.foreign_company)
        with self.assertRaises(ValidationError),transaction.atomic():self.other.production_viewers.add(outsider)
        form=CompanyUserAccessForm({'role':self.user.role_id,'production_additional_objects':[self.foreign.pk]},instance=self.user,company=self.company)
        self.assertFalse(form.is_valid());self.assertIn('production_additional_objects',form.errors)
        group=AccessGroup.objects.create(company=self.company,name='Production access group',role=self.user.role)
        group.assigned_objects.add(self.obj);group.production_additional_objects.add(self.other)
        form=CompanyUserAccessForm({'access_group':group.pk,'assigned_objects':[self.hidden.pk],'production_additional_objects':[self.hidden.pk],'is_active':'on'},instance=self.user,company=self.company)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        self.assertSetEqual(set(self.user.assigned_objects.all()),{self.obj});self.assertSetEqual(set(self.user.production_additional_objects.all()),{self.other})
        group.production_additional_objects.clear();self.assertFalse(self.user.production_additional_objects.exists())
        with self.assertRaises(ValidationError),transaction.atomic():group.production_additional_objects.add(self.foreign)

    def test_ad_membership_loss_revokes_additional_rights(self):
        group=AccessGroup.objects.create(company=self.company,name='AD production group',role=self.user.role,ad_group_dn='CN=Production,DC=example,DC=org');group.production_additional_objects.add(self.other)
        self.user.access_group=group;self.user.access_group_from_ad=True;self.user.save()
        map_directory_group(self.user,{'group_dns':[]})
        self.assertFalse(self.user.production_additional_objects.exists());self.assertIsNone(self.user.role_id)
