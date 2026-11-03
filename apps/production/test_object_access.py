"""Production visibility never grants entry rights or a daily task subscription."""
import json
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
        cls.other_work=ProjectWork.objects.create(company=cls.company,section=section,name='Read only work',unit='m')
        cls.fact=DailyFact.objects.create(company=cls.company,project_work=cls.other_work,date=cls.day,actual_quantity=7,reported_by=cls.users['ADMIN'])
        cls.person=EmployeeActivity.objects.create(company=cls.company,construction_object=cls.other,date=cls.day,employee=cls.employee,position=cls.employee.position,entry_direction='works',reported_by=cls.users['ADMIN'])

    def setUp(self):
        self.user=self.users['DISPATCHER']
        self.user.production_view_objects.add(self.other)
        self.client.force_login(self.user)
        self.query={'construction_object':self.other.pk,'date':str(self.day),'section':'works'}

    def test_visible_union_without_expanding_default_write_scope(self):
        self.assertSetEqual(set(scope_queryset(ConstructionObject.objects.all(),self.user,production_read=True)),{self.obj,self.other})
        self.assertSetEqual(set(scope_queryset(ConstructionObject.objects.all(),self.user)),{self.obj})
        self.user.production_view_objects.add(self.obj)
        self.assertEqual(scope_queryset(ConstructionObject.objects.all(),self.user,production_read=True).count(),2)

    def test_view_only_day_contains_other_authors_fact_and_disabled_controls_without_creating_fact_state(self):
        self.assertFalse(FactSectionDay.objects.filter(construction_object=self.other).exists())
        page=self.client.get(self.url,self.query)
        self.assertEqual(page.status_code,200)
        self.assertFalse(page.context['editable']);self.assertFalse(page.context['own_entry'])
        self.assertContains(page,'Только просмотр.')
        self.assertNotContains(page,'Ввод и повторные исправления доступны')
        self.assertNotContains(page,'Сейчас в форме:')
        self.assertContains(page,'<fieldset disabled>',html=False)
        self.assertNotContains(page,'production/production_autosave.js')
        self.assertNotContains(page,'production/links_outbox.js')
        works=next(g['formset'] for g in page.context['groups'] if g['prefix']=='works')
        self.assertIn(self.fact.pk,[row.instance.pk for row in works])
        saved=next(row for row in works if row.instance.pk==self.fact.pk)
        self.assertTrue(saved.fields['project_work'].queryset.filter(pk=self.other_work.pk).exists())
        self.assertFalse(FactSectionDay.objects.filter(construction_object=self.other).exists())
        self.assertFalse(ProductionEntryDay.objects.filter(construction_object=self.other,dispatcher=self.user).exists())

    def test_tasks_only_for_fill_objects_and_reassignment_preserves_view_but_cancels_old_tasks(self):
        generate_daily(self.user,today=self.day+timedelta(days=1))
        tasks=WorkTask.objects.filter(assigned_to=self.user,kind='FACT')
        self.assertSetEqual(set(tasks.values_list('construction_object_id',flat=True)),{self.obj.pk})
        self.assertFalse(DailyTaskCursor.objects.filter(user=self.user,construction_object=self.other).exists())
        self.user.production_view_objects.add(self.obj)
        self.user.assigned_objects.set([self.other])
        generate_daily(self.user,today=self.day+timedelta(days=1))
        self.assertEqual(tasks.get(construction_object=self.obj).status,'SUPERSEDED')
        self.assertEqual(tasks.get(construction_object=self.other).status,'OPEN')
        page=self.client.get(self.url,{**self.query,'construction_object':self.obj.pk})
        self.assertFalse(page.context['editable'])
        self.assertTrue(self.client.get(self.url,self.query).context['editable'])

    def test_no_fill_objects_still_can_view_without_daily_tasks(self):
        self.user.assigned_objects.clear()
        self.assertEqual(generate_daily(self.user,today=self.day+timedelta(days=1)),0)
        self.assertEqual(self.client.get(self.url,self.query).status_code,200)
        self.assertFalse(WorkTask.objects.filter(assigned_to=self.user,kind='FACT').exists())

    def test_forged_mutation_endpoints_reject_view_only_object_and_do_not_write(self):
        requests=[('production:fact_day_workspace',self.query,False),
            ('production:production_links',{**self.query,'activity':self.person.pk,'action':'add','fact':self.fact.pk},True),
            ('production:production_links',{**self.query,'protocol':'queued-v1','actor_id':self.user.pk,'company_id':self.company.pk,'commands':[{'id':str(uuid4()),'activity':self.person.pk,'action':'add','fact':self.fact.pk}]},True),
            ('production:production_copy_previous',self.query,True),
            ('production:work_claims',{**self.query,'session':str(uuid4()),'action':'take','key':f'work:{self.other_work.pk}'},True),
            ('production:actuation_reason_propose',{**self.query,'label':'Must not be proposed'},False),
            ('production:actuation_documents',self.query,False)]
        for route,payload,as_json in requests:
            with self.subTest(route=route,protocol=payload.get('protocol')):
                response=self.client.post(reverse(route),json.dumps(payload) if as_json else payload,content_type='application/json' if as_json else 'application/x-www-form-urlencoded') if as_json else self.client.post(reverse(route),payload)
                self.assertIn(response.status_code,{400,403,404,409})
        self.fact.refresh_from_db();self.assertEqual(self.fact.actual_quantity,7)
        for model in [WorkFactClaim,EmployeeFactClaim,EmployeeWorkLink,ProductionLinkReceipt,FactSectionDay,ProductionEntryDay]:
            self.assertEqual(model.objects.count(),0,model.__name__)

    def test_report_and_exports_allow_view_object_but_hidden_and_other_company_are_denied(self):
        for route in ['production:production_report_download','production:smg_period_detail','production:smg_period_export','production:equipment_report']:
            with self.subTest(route=route):
                response=self.client.get(reverse(route),self.query)
                self.assertEqual(response.status_code,200)
                if response.streaming:response.close()
        for obj in [self.hidden,self.foreign]:
            page=self.client.get(self.url,{**self.query,'construction_object':obj.pk})
            self.assertNotIn('object',page.context)
            self.assertEqual(self.client.get(reverse('production:production_report_download'),{**self.query,'construction_object':obj.pk}).status_code,400)
        self.user.production_view_objects.clear()
        self.assertNotIn('object',self.client.get(self.url,self.query).context)

    def test_other_roles_do_not_gain_production_rights_from_extra_view_objects(self):
        for code in ['PERSONNEL_DISPATCHER','MECHANIZATION_DISPATCHER']:
            user=self.users[code];user.production_view_objects.add(self.other)
            self.assertFalse(scope_queryset(ConstructionObject.objects.all(),user,production_read=True).filter(pk=self.other.pk).exists())

    def test_all_direction_dispatcher_read_only_on_each_direction(self):
        user=User.objects.create_user(username='all-object-scope',company=self.company,role=Role.objects.get(code='ALL_FACT_DISPATCHER'))
        user.assigned_objects.add(self.obj);user.production_view_objects.add(self.other)
        self.client.force_login(user)
        for section in ['works','labor','mechanization']:
            page=self.client.get(self.url,{**self.query,'section':section})
            self.assertEqual(page.status_code,200);self.assertFalse(page.context['editable'])

    def test_company_guards_on_direct_and_reverse_assignments_and_company_change(self):
        with self.assertRaises(ValidationError),transaction.atomic():self.user.production_view_objects.add(self.foreign)
        outsider=User.objects.create_user(username='outsider-object-scope',company=self.foreign_company)
        with self.assertRaises(ValidationError),transaction.atomic():self.other.production_viewers.add(outsider)
        self.user.assigned_objects.clear();self.user.company=self.foreign_company
        with self.assertRaises(ValidationError),transaction.atomic():self.user.save()

    def test_individual_form_and_group_inheritance_override_forged_view_selection(self):
        form=CompanyUserAccessForm({'role':self.user.role_id,'assigned_objects':[self.obj.pk],'production_view_objects':[self.other.pk],'is_active':'on'},instance=self.user,company=self.company)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        self.assertSetEqual(set(self.user.production_view_objects.all()),{self.other})
        form=CompanyUserAccessForm({'role':self.user.role_id,'production_view_objects':[self.foreign.pk]},instance=self.user,company=self.company)
        self.assertFalse(form.is_valid());self.assertIn('production_view_objects',form.errors)
        group=AccessGroup.objects.create(company=self.company,name='Production view group',role=self.user.role)
        group.assigned_objects.add(self.obj);group.production_view_objects.add(self.other)
        form=CompanyUserAccessForm({'access_group':group.pk,'assigned_objects':[self.hidden.pk],'production_view_objects':[self.hidden.pk],'is_active':'on'},instance=self.user,company=self.company)
        self.assertTrue(form.is_valid(),form.errors);form.save()
        self.assertSetEqual(set(self.user.assigned_objects.all()),{self.obj})
        self.assertSetEqual(set(self.user.production_view_objects.all()),{self.other})
        group.production_view_objects.clear();self.assertFalse(self.user.production_view_objects.exists())
        with self.assertRaises(ValidationError),transaction.atomic():group.production_view_objects.add(self.foreign)
        self.client.force_login(self.users['ADMIN'])
        page=self.client.get(reverse('accounts:user_access',args=[self.user.pk]))
        self.assertTrue(page.context['form'].fields['production_view_objects'].disabled)

    def test_ad_membership_loss_revokes_additional_view_rights(self):
        group=AccessGroup.objects.create(company=self.company,name='AD production group',role=self.user.role,ad_group_dn='CN=Production,DC=example,DC=org')
        group.production_view_objects.add(self.other)
        self.user.access_group=group;self.user.access_group_from_ad=True;self.user.save()
        map_directory_group(self.user,{'group_dns':[]})
        self.assertFalse(self.user.production_view_objects.exists())
        self.assertIsNone(self.user.role_id)
