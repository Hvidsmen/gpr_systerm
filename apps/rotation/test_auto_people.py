import importlib
from types import SimpleNamespace
from django.apps import apps
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from .tests import RotationTests
from .models import RotationPlan, RotationPerson, RotationStatus
from .services import refresh_demand, ensure_people, matrix


class AutoPeopleTests(TestCase):
    setUpTestData = classmethod(RotationTests.setUpTestData.__func__)

    def setUp(self):
        RotationTests.setUp(self)

    def test_refresh_builds_named_workers_and_continuous_coverage_once(self):
        refresh_demand(self.plan)
        self.assertEqual(list(self.position.people.values_list('name',flat=True)),['Работник 1','Работник 2'])
        self.assertEqual(set(matrix(self.plan,self.start,self.end)[1][0]['present']),{1})
        self.assertEqual(ensure_people(self.plan),0)
        refresh_demand(self.plan)
        self.assertEqual(self.position.people.count(),2)

    def test_create_plan_needs_no_generate_action(self):
        response=self.client.post(reverse('rotation:plan_create'),{'title':'Автоматический','source':self.source.pk,'start':self.start,'end':self.end})
        self.assertEqual(response.status_code,302)
        plan=RotationPlan.objects.get(title='Автоматический')
        self.assertEqual(list(plan.positions.get().people.values_list('name',flat=True)),['Работник 1','Работник 2'])

    def test_existing_person_and_manual_status_remain_unchanged(self):
        person=RotationPerson.objects.create(company=self.company,position=self.position,name='Сотрудник А',on_days=30,off_days=60,anchor=self.start)
        RotationStatus.objects.create(company=self.company,person=person,day=self.start,status='OFF')
        refresh_demand(self.plan)
        self.assertEqual(self.position.people.count(),1)
        person.refresh_from_db()
        self.assertEqual((person.name,person.on_days,person.off_days),('Сотрудник А',30,60))
        self.assertEqual(person.overrides.count(),1)

    def test_zero_demand_has_no_people_and_future_positive_demand_creates_them(self):
        self.plan.demand=[];self.plan.save()
        self.assertEqual(ensure_people(self.plan),0)
        refresh_demand(self.plan)
        self.assertEqual(self.position.people.count(),2)

    def test_migration_fills_existing_empty_roles_and_is_idempotent(self):
        migrate=importlib.import_module('apps.rotation.migrations.0003_auto_people').backfill
        migrate(apps,SimpleNamespace(connection=connection))
        self.assertEqual(list(self.position.people.values_list('name',flat=True)),['Работник 1','Работник 2'])
        migrate(apps,SimpleNamespace(connection=connection))
        self.assertEqual(self.position.people.count(),2)
