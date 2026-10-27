from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from apps.resources.models import Brigade,BrigadeMerge,EquipmentType
from apps.resources.position_catalog import brigade_for
from . import test_work_resources as fixtures
from .models import ResourceMonthAllocation
from .plan_workbench import add_month_resource
from .daily_schedule_table import add_entry
from .resource_catalogue_links import catalogue_links
from .independent_smg import create


class ResourceCatalogueLinkTests(TestCase):
    setUpTestData=classmethod(fixtures.WorkResourceTests.setUpTestData.__func__)
    prepare_owner=fixtures.WorkResourceTests.prepare_owner
    def setUp(self):
        fixtures.WorkResourceTests.setUp(self)
        self.schedule=create(self.planner,self.obj,fixtures.JAN)
        self.data={'kind':'equipment','equipment':self.equipment,'rate':0}
        add_month_resource(self.planner,self.version,fixtures.JAN,self.data)
        add_entry(self.planner,self.schedule,self.data)
        self.position_brigade=brigade_for(self.position)
        self.urls=[reverse('planning:workspace_edit',args=[self.version.pk]),reverse('planning:daily_schedule_detail',args=[self.schedule.pk])]

    def test_raw_and_crew_positions_and_machines_link_to_editable_catalogue(self):
        for url in self.urls:
            response=self.client.get(url)
            for kind,pk in [('brigade',self.position_brigade),('equipment_type',self.equipment.pk)]:
                self.assertContains(response,'href="'+reverse('resources:'+kind+'_update',args=[pk])+'" target="_blank" rel="noopener noreferrer" data-resource-catalogue')

    def test_captured_assignment_resources_have_links_too(self):
        fixtures.WorkResourceTests.assignment(self,self.version)
        fixtures.WorkResourceTests.assignment(self,self.schedule)
        for url in self.urls:
            response=self.client.get(url)
            entries=[e for section in response.context['plan_sections'] for group in section['groups'] for e in group['entries'] if e.metadata.get('resource_assignment_id')]
            self.assertTrue(entries)
            self.assertTrue(all(e.catalogue_url for e in entries if e.kind in {'labor','equipment'}))

    def test_reader_gets_catalogue_view_links_without_access_to_edit(self):
        observer=User.objects.create_user(username='catalogue-link-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        gear=self.version.resource_allocations.get(kind='equipment');gear.count=1;gear.save()
        from .table_resource_crew import sync_monthly
        sync_monthly(self.planner,self.version,fixtures.JAN)
        for user,urls in [(self.manager,self.urls),(observer,self.urls)]:
            self.client.force_login(user)
            for url in urls:
                response=self.client.get(url)
                self.assertContains(response,'href="/resources/brigades/?name=')
                self.assertNotContains(response,'href="'+reverse('resources:brigade_update',args=[self.position_brigade])+'"')

    def test_links_follow_alias_and_never_use_another_company(self):
        target=Brigade.objects.create(company=self.company,name='Машинист экскаватора')
        source=Brigade.objects.create(company=self.company,name='Старая должность')
        BrigadeMerge.objects.create(company=self.company,source=source,target=target,created_by=self.planner,audit={'test':True})
        foreign=Brigade.objects.create(company=Company.objects.create(name='Foreign links'),name='Чужая')
        resolve=catalogue_links(self.company)
        self.assertEqual(resolve('labor',source.pk)['catalogue_url'],reverse('resources:brigade_update',args=[target.pk]))
        self.assertEqual(resolve('labor',foreign.pk),{})
        self.assertEqual(resolve('work',source.pk),{})

    def test_month_fill_is_available_for_manual_resources_but_not_assignments_or_work(self):
        fixtures.WorkResourceTests.assignment(self,self.schedule)
        response=self.client.get(self.urls[1])
        for section in response.context['plan_sections']:
            for group in section['groups']:
                for entry in group['entries']:
                    fills=[row for row in entry.comparison if row.get('month_fill')]
                    if entry.kind in {'labor','equipment'} and not entry.metadata.get('resource_assignment_id'):
                        self.assertEqual(len(fills),1)
                        self.assertEqual(fills[0]['month_fill_value'],0)
                    else:self.assertFalse(fills)
        self.assertContains(response,'data-smg-fill-month')
