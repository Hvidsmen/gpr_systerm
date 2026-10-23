from datetime import date
from decimal import Decimal
from copy import deepcopy
from django.test import TestCase
from django.urls import reverse,resolve
from django.core.exceptions import ValidationError,PermissionDenied
from apps.accounts.models import Company,Role,User
from apps.works.progress import work_specification
from . import test_workspace as fixtures
from .models import GlobalPlanVersion,DailySchedule,DailyScheduleEntry
from .workspace_services import WorkspaceService
from .daily_schedule import create,save_entry,activate as apply_schedule,overlay,identities,differences
from .approval_test_helpers import estimator_check

def activate(user,schedule):
    estimator_check(schedule,user)
    manager=schedule.preparation.manager
    from .daily_schedule_approval import requires_approval,transition
    if requires_approval(schedule):
        schedule=transition(manager,schedule,'submit')
        for role,action in [('PRODUCTION_HEAD','approve_production'),('HR_HEAD','approve_hr'),('TECH_HEAD','approve_tech'), ('SECURITY_HEAD','approve_security'),('CEO','approve')]:
            actor=User.objects.filter(company=user.company,role__code=role,is_active=True).first()
            schedule=transition(actor,schedule,action)
    return apply_schedule(manager,schedule)
from .report_services import select_sources


class DailyScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures.WorkspaceTests.setUpTestData.__func__(cls)

    def test_creation_url_opens_form_instead_of_post_only_action(self):
        self.prepare()
        self.client.force_login(self.planner)
        url=reverse('planning:daily_schedule_create',args=[self.source.pk])
        self.assertEqual(resolve(url).url_name,'daily_schedule_create')
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertTemplateUsed(response,'planning/daily_schedule_create.html')
        self.assertEqual(resolve(reverse('planning:global_delete',args=[self.source.pk])).url_name,'global_delete')
        self.assertEqual(resolve(reverse('planning:global_action',args=[self.source.pk,'submit'])).url_name,'global_action')

    def test_detail_matrix_groups_days_and_preserves_monthly_comparison(self):
        self.prepare()
        save_entry(self.planner,self.entry,self.values(self.entry,120),0)
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.context['days']),28)
        sections={section['kind']:section for section in response.context['sections']}
        work=sections['work']['groups'][0]['entries'][0]
        row=work.comparison[0]
        self.assertEqual(row['before'],100);self.assertEqual(row['after'],120)
        self.assertEqual(row['cells'][0]['before'],100);self.assertEqual(row['cells'][1]['after'],120)
        self.assertEqual(sections['labor']['totals'][0]['before'],Decimal(2)/28)
        self.assertContains(response,'Развернуть всё');self.assertContains(response,'СМГ за месяц')
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)

    def test_composite_daily_matrix_and_shift_only_deviation(self):
        self.prepare(composite=True)
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk]))
        entry=response.context['sections'][0]['groups'][0]['entries'][0]
        self.assertEqual(len(entry.comparison),3)
        self.assertEqual(entry.comparison[0]['cells'][0]['after'],10)
        self.assertFalse(entry.has_deviation)
        for row in self.entry.rows:row['date']='2026-02-02'
        self.entry.save(update_fields=['rows'])
        response=self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk]))
        entry=response.context['sections'][0]['groups'][0]['entries'][0]
        self.assertTrue(entry.has_deviation)
        self.assertEqual(entry.comparison[0]['delta'],0)

    def prepare(self,composite=False):
        self.work=self.composite if composite else self.simple
        workspace=WorkspaceService.create(self.planner,self.obj,'Daily',fixtures.JAN,date(2026,3,31))
        self.source=workspace.baseline_version
        spec=work_specification(self.work)
        spec['plans']=[dict(date='2026-02-01',item_id=i.pk,quantity=str(i.quantity_per_unit*10),value='100',origin='PLAN') for i in self.work.items.all()] if composite else [dict(date='2026-02-01',item_id=None,quantity='100',value='1000',origin='PLAN')]
        spec['daily']=[dict(date='2026-02-01',quantity='10' if composite else '100',cumulative='10' if composite else '100')]
        snapshot={'works':[spec],'resources':{'labor':[dict(date='2026-02-01',brigade_id=self.brigade.pk,label='Workers',planned_count=2,planned_hours='16',hourly_rate='100',origin='PLAN')],'equipment':[],'fuel':[]}}
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='APPROVED',snapshot=snapshot)
        self.source.refresh_from_db()
        self.original=deepcopy(self.source.snapshot)
        self.schedule=create(self.planner,self.source,fixtures.FEB)
        self.entry=self.schedule.entries.get(kind='work')
        self.client.force_login(self.planner)

    def values(self,entry,amount=100,day=2):
        return [{'quantity':Decimal(amount) if d.day==day else Decimal(0)} for d,item in identities(entry)]

    def test_daily_edit_month_deviation_and_immutable_source(self):
        self.prepare()
        updated=save_entry(self.planner,self.entry,self.values(self.entry),0)
        self.assertFalse(differences(updated,updated.rows))
        changed=save_entry(self.planner,updated,self.values(updated,120),1)
        self.assertEqual(list(differences(changed,changed.rows).values()),[Decimal(20)])
        self.source.refresh_from_db();self.assertEqual(self.source.snapshot,self.original)
        activate(self.planner,self.schedule)
        effective=overlay(self.source,self.original,fixtures.FEB,date(2026,2,28))
        self.assertEqual(sum(Decimal(r['quantity']) for r in effective['works'][0]['daily']),120)
        self.assertEqual(Decimal(next(r for r in effective['works'][0]['daily'] if r['date']=='2026-02-02')['quantity']),120)
        self.assertEqual(self.source.snapshot,self.original)
        with self.assertRaises(PermissionDenied):save_entry(self.planner,changed,self.values(changed),2)

    def test_apply_only_operational_default_and_revision(self):
        self.prepare()
        save_entry(self.planner,self.entry,self.values(self.entry,130),0)
        filters={'start':fixtures.FEB,'end':date(2026,2,28),'mode':'latest'}
        before=select_sources(self.planner,filters,[self.obj])[self.obj.pk][0].snapshot
        self.assertFalse(before.get('daily_schedule_applied',False))
        activate(self.planner,self.schedule)
        after=select_sources(self.planner,filters,[self.obj])[self.obj.pk][0].snapshot
        self.assertTrue(after['daily_schedule_applied'])
        baseline=select_sources(self.planner,{**filters,'mode':'baseline'},[self.obj])[self.obj.pk][0].snapshot
        self.assertFalse(baseline.get('daily_schedule_applied',False))
        explicit=select_sources(self.planner,{**filters,f'version_{self.obj.pk}':self.source},[self.obj])[self.obj.pk][0].snapshot
        self.assertFalse(explicit.get('daily_schedule_applied',False))
        new=create(self.planner,self.source,fixtures.FEB)
        self.assertNotEqual(new.pk,self.schedule.pk)
        self.assertEqual(new.revision,2)
        self.assertEqual(create(self.planner,self.source,fixtures.FEB).pk,new.pk)
        entry=new.entries.get(kind='work')
        self.assertTrue(differences(entry,entry.rows))
        activate(self.planner,new)
        self.schedule.refresh_from_db();self.assertEqual(self.schedule.status,'ARCHIVED')
        self.assertEqual(DailySchedule.objects.filter(status='APPLIED').count(),1)

    def test_composite_and_resource_differences(self):
        self.prepare(composite=True)
        rows=[{'quantity':Decimal(item['norm'])*12 if day.day==3 else Decimal(0)} for day,item in identities(self.entry)]
        entry=save_entry(self.planner,self.entry,rows,0)
        self.assertEqual(len(differences(entry,entry.rows)),2)
        labor=self.schedule.entries.get(kind='labor')
        values=[{'planned_count':Decimal(3) if d.day==2 else Decimal(0),'planned_hours':Decimal(24) if d.day==2 else Decimal(0)} for d,item in identities(labor)]
        labor=save_entry(self.planner,labor,values,0)
        self.assertEqual(differences(labor,labor.rows)['planned_count'],1)
        activate(self.planner,self.schedule)
        effective=overlay(self.source,self.original,fixtures.FEB,date(2026,2,28))
        self.assertEqual(sum(Decimal(r['quantity']) for r in effective['works'][0]['daily']),12)
        self.assertEqual(sum(Decimal(str(r['planned_hours'])) for r in effective['resources']['labor']),24)

    def test_permissions_source_and_concurrent_save(self):
        self.prepare()
        changed=save_entry(self.planner,self.entry,self.values(self.entry),0)
        with self.assertRaises(ValidationError):save_entry(self.planner,changed,self.values(changed),0)
        bad=self.values(changed);bad[0]['quantity']=Decimal(-1)
        with self.assertRaises(ValidationError):save_entry(self.planner,changed,bad,1)
        with self.assertRaises(ValidationError):create(self.planner,self.source,date(2027,1,1))
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(status='DRAFT')
        self.source.refresh_from_db()
        with self.assertRaises(ValidationError):create(self.planner,self.source,fixtures.MAR)
        other=Company.objects.create(name='Other daily')
        observer=User.objects.create_user(username='daily-other',company=other,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        self.assertEqual(self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk])).status_code,404)
        self.assertEqual(self.client.post(reverse('planning:daily_schedule_apply',args=[self.schedule.pk])).status_code,403)

    def test_editor_save_warning_and_observer_readonly(self):
        self.prepare()
        url=reverse('planning:daily_schedule_edit',args=[self.entry.pk])
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        data={'form-TOTAL_FORMS':28,'form-INITIAL_FORMS':28,'form-MIN_NUM_FORMS':0,'form-MAX_NUM_FORMS':28,'revision':0}
        for i in range(28):data[f'form-{i}-quantity']='120' if i==1 else '0'
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,302)
        self.assertContains(self.client.get(reverse('planning:daily_schedule_detail',args=[self.schedule.pk])),'Месячные итоги отличаются')
        observer=User.objects.create_user(username='daily-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(observer)
        self.assertEqual(self.client.get(url).status_code,200)
        self.assertEqual(self.client.post(url,data).status_code,403)

    def test_equipment_fuel_and_partial_period_overlay(self):
        self.prepare()
        equipment=DailyScheduleEntry.objects.create(schedule=self.schedule,key='equipment:test',kind='equipment',label='Crane',original=[dict(date='2026-02-01',equipment_type_id=self.equipment.pk,equipment_number='A',planned_count=1,planned_machine_hours='8',hourly_rate='10',label='Crane')],rows=[])
        fuel=DailyScheduleEntry.objects.create(schedule=self.schedule,key='fuel:test',kind='fuel',label='Diesel',original=[dict(date='2026-02-28',fuel_type='DIESEL',equipment_ref='',planned_liters='100',planned_balance='20',price_per_liter='2',label='Diesel')],rows=[])
        save_entry(self.planner,equipment,[{'planned_count':Decimal(2) if day.day==2 else Decimal(0),'planned_machine_hours':Decimal(16) if day.day==2 else Decimal(0)} for day,item in identities(equipment)],0)
        save_entry(self.planner,fuel,[{'planned_liters':Decimal(120) if day.day==2 else Decimal(0),'planned_balance':Decimal(30)} for day,item in identities(fuel)],0)
        activate(self.planner,self.schedule)
        effective=overlay(self.source,self.original,date(2026,2,2),date(2026,2,2))
        self.assertEqual(len(effective['resources']['equipment']),1)
        self.assertEqual(Decimal(effective['resources']['equipment'][0]['planned_machine_hours']),16)
        self.assertEqual(Decimal(effective['resources']['fuel'][0]['planned_liters']),120)

    def test_monthly_source_month_is_locked(self):
        self.prepare()
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(version_kind='FORECAST',planning_month=fixtures.FEB)
        self.source.refresh_from_db()
        with self.assertRaises(ValidationError):create(self.planner,self.source,fixtures.MAR)

    def test_composite_history_keeps_monthly_completion_correct(self):
        self.prepare(composite=True)
        self.schedule.delete()
        snapshot=deepcopy(self.original)
        snapshot['works'][0]['plans'] += [dict(date='2026-01-15',item_id=i.pk,quantity=str(i.quantity_per_unit*10),value='100',origin='PLAN') for i in self.work.items.all()]
        GlobalPlanVersion.objects.filter(pk=self.source.pk).update(snapshot=snapshot)
        schedule=create(self.planner,self.source,fixtures.FEB)
        entry=schedule.entries.get(kind='work')
        rows=[{'quantity':Decimal(item['norm'])*10 if day.day==3 else Decimal(0)} for day,item in identities(entry)]
        save_entry(self.planner,entry,rows,0)
        activate(self.planner,schedule)
        effective=overlay(self.source,self.original,fixtures.FEB,date(2026,2,28))
        self.assertEqual(sum(Decimal(r['quantity']) for r in effective['works'][0]['daily']),10)
