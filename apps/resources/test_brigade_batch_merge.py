from datetime import date
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from apps.planning import test_workspace as fixtures
from apps.planning.models import WorkResourceAssignment
from apps.accounts.models import OneCConnection
from .models import Brigade,BrigadeMerge,Employee,EquipmentCrewPosition
from .brigade_batch_merge import preview
from .position_catalog import position_index,position_key


class BatchJobMergeTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkspaceTests.setUpTestData.__func__)
    create=fixtures.WorkspaceTests.create

    def setUp(self):
        self.target=self.brigade
        self.source=Brigade.objects.create(company=self.company,name=self.target.name.upper())
        self.client.force_login(self.planner)
        self.url=reverse('resources:brigade_batch_merge')

    def selection(self):
        response=self.client.get(self.url);self.assertEqual(response.status_code,200)
        return response.context['selection']

    def apply(self,selection=None):return self.client.post(self.url,{'selection':selection or self.selection(),'confirm':'on'})

    def test_first_id_keeps_settings_and_moves_employee_fk_and_missing_zup_link(self):
        p=self.source.position_mapping.position;target=self.target.position_mapping.position
        employee=Employee.objects.create(company=self.company,first_name='Employee',position=p,is_active=False)
        OneCConnection.objects.create(company=self.company,server='sql',database='zup')
        missing=Employee.objects.create(company=self.company,zup_ref='a'*32,zup_server='sql',zup_database='zup',zup_synced_at=timezone.now(),zup_card={'staffing_position':self.target.name+' /Отдел/'})
        token=self.selection();self.assertFalse(BrigadeMerge.objects.exists())
        self.assertEqual(self.apply(token).status_code,302)
        employee.refresh_from_db();missing.refresh_from_db()
        self.assertEqual(employee.position,target);self.assertFalse(employee.is_active)
        self.assertEqual(missing.position,target)
        self.assertEqual(BrigadeMerge.objects.get().target,self.target)
        self.assertEqual(position_index(self.company.pk)[position_key(self.target.name)],[target])
        card=self.client.get(reverse('resources:employee_detail',args=[missing.pk]));self.assertContains(card,'Должность в справочнике:');self.assertContains(card,'Связана')
        listing=self.client.get(reverse('resources:employee_list'),{'catalogue_position':target.pk,'include_archived':1})
        self.assertEqual({e.pk for e in listing.context['employees']},{employee.pk,missing.pk})

    def test_live_equipment_and_captured_crews_keep_counts(self):
        ws=self.create();target=self.target.position_mapping.position;source=self.source.position_mapping.position
        for position,count in [(target,1),(source,2)]:EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=position,count=count)
        crew=[{'position_id':p.pk,'brigade_id':b.pk,'name':b.name,'count':n,'is_replacement':False,'hourly_rate':'100'} for p,b,n in [(target,self.target,1),(source,self.source,2)]]
        a=WorkResourceAssignment.objects.create(company=self.company,version=ws.baseline_version,equipment_type=self.equipment,start_date=date(2026,1,1),end_date=date(2026,1,2),crew_snapshot=crew)
        self.assertEqual(self.apply().status_code,302)
        self.assertEqual(EquipmentCrewPosition.objects.get(equipment_type=self.equipment).count,3)
        a.refresh_from_db();self.assertEqual(len(a.crew_snapshot),1);self.assertEqual(a.crew_snapshot[0]['count'],3)
        self.assertEqual(a.crew_snapshot[0]['hourly_rate'],'100')

    def test_catalogue_change_and_missing_confirmation_do_not_merge(self):
        token=self.selection();self.source.name+='.';self.source.save()
        self.assertEqual(self.apply(token).status_code,400);self.assertFalse(BrigadeMerge.objects.exists())
        self.assertEqual(self.client.post(self.url,{'selection':self.selection()}).status_code,400)
        self.assertFalse(BrigadeMerge.objects.exists())

    def test_blocked_first_record_is_not_silently_replaced(self):
        self.target.is_active=False;self.target.save()
        data=preview(self.company)
        self.assertEqual(data['blocked'],1);self.assertFalse(data['eligible'])
        self.assertEqual(self.apply().status_code,400);self.assertFalse(BrigadeMerge.objects.exists())

    def test_company_and_role_scope(self):
        from apps.accounts.models import Company
        foreign=Brigade.objects.create(company=Company.objects.create(name='Foreign'),name=self.target.name)
        self.assertEqual(self.apply().status_code,302)
        foreign.refresh_from_db();self.assertTrue(foreign.is_active)
        self.client.force_login(self.manager);self.assertEqual(self.client.get(self.url).status_code,403)

    def test_batch_rolls_back_all_groups_on_late_error(self):
        from django.core.exceptions import ValidationError
        first=Brigade.objects.create(company=self.company,name='Another');Brigade.objects.create(company=self.company,name='ANOTHER')
        from .brigade_merge import merge_brigades
        calls=[]
        def fail_second(*args):
            calls.append(1)
            if len(calls)>1:raise ValidationError('Conflict')
            return merge_brigades(*args)
        with patch('apps.resources.brigade_batch_merge.merge_brigades',side_effect=fail_second):self.assertEqual(self.apply().status_code,400)
        self.assertFalse(BrigadeMerge.objects.exists());self.source.refresh_from_db();self.assertTrue(self.source.is_active)

    def test_repair_old_alias_employee_links_without_zup_connection(self):
        old=self.source.position_mapping.position
        employee=Employee.objects.create(company=self.company,position=old,first_name='Manual',is_active=False)
        self.assertEqual(self.apply().status_code,302)
        Employee.objects.filter(pk=employee.pk).update(position=old)
        response=self.client.post(reverse('resources:brigade_link_employees'))
        self.assertEqual(response.status_code,302)
        employee.refresh_from_db()
        self.assertEqual(employee.position_id,self.target.position_mapping.position_id)
        self.assertFalse(employee.is_active)

    def test_approved_assignment_is_unchanged_and_new_copy_resolves_alias(self):
        from apps.planning.work_resources import copy_assignments
        from apps.planning.independent_smg import create
        ws=self.create();old=self.source.position_mapping.position
        crew=[{'position_id':old.pk,'brigade_id':self.source.pk,'name':self.source.name,'count':1,'is_replacement':False,'hourly_rate':'135'}]
        row=WorkResourceAssignment.objects.create(company=self.company,version=ws.baseline_version,position=old,start_date=date(2026,1,1),end_date=date(2026,1,2),crew_snapshot=crew)
        type(ws.baseline_version).objects.filter(pk=ws.baseline_version.pk).update(status='APPROVED')
        self.assertEqual(self.apply().status_code,302)
        row.refresh_from_db();self.assertEqual(row.position_id,old.pk);self.assertEqual(row.crew_snapshot,crew)
        schedule=create(self.planner,self.obj,date(2026,1,1))
        copy_assignments(ws.baseline_version,schedule)
        copied=schedule.resource_assignments.get()
        self.assertEqual(copied.position_id,self.target.position_mapping.position_id)
        self.assertEqual(copied.crew_snapshot[0]['hourly_rate'],'135')
