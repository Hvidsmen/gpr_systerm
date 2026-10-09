from datetime import date, timedelta
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject
from apps.resources.models import Brigade
from apps.planning.models import GlobalPlanVersion
from .models import RotationPlan, RotationRole, RotationPerson
from .services import refresh_demand, generate_people, on_shift, matrix


class RotationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Rotation')
        cls.user = User.objects.create_user(username='rotation-planner', company=cls.company, role=Role.objects.get(code='PLANNER'))
        cls.project = Project.objects.create(company=cls.company, name='Project')
        cls.obj = ConstructionObject.objects.create(company=cls.company, project=cls.project, name='Object')
        cls.brigade = Brigade.objects.create(company=cls.company, name='Водитель')
        cls.start = date(2026, 1, 1)
        cls.end = date(2026, 6, 30)
        # Use a real draft version; its resource snapshot can also be built on demand.
        cls.source = GlobalPlanVersion.objects.create(company=cls.company, construction_object=cls.obj,
            version_number=1, start_date=cls.start, end_date=cls.end)
        cls.source.snapshot = {'resources': {'labor': [{'brigade_id':cls.brigade.pk, 'date':(cls.start+timedelta(days=i)).isoformat(), 'planned_workers':1} for i in range(181)]}}
        GlobalPlanVersion.objects.filter(pk=cls.source.pk).update(snapshot=cls.source.snapshot)
        from apps.production.models import LaborPlan
        LaborPlan.objects.bulk_create([LaborPlan(company=cls.company, construction_object=cls.obj, brigade=cls.brigade, date=cls.start+timedelta(days=i), planned_workers=1) for i in range(181)])
        cls.foreign = Company.objects.create(name='Foreign')
        cls.other_user = User.objects.create_user(username='rotation-other', company=cls.foreign, role=Role.objects.get(code='PLANNER'))
        cls.foreign_brigade = Brigade.objects.create(company=cls.foreign, name='Водитель')

    def setUp(self):
        self.client.force_login(self.user)
        self.plan = RotationPlan.objects.create(company=self.company, source=self.source, title='Перевахта', start=self.start, end=self.end)
        refresh_demand(self.plan)
        self.position = self.plan.positions.get()
        # Generator tests start from an empty roster. Production refresh creates it automatically.
        self.position.people.all().delete()

    def test_continuous_45_45_has_two_people_and_no_month_reset(self):
        self.assertEqual(generate_people(self.position), 2)
        first, second = list(self.position.people.all())
        self.assertTrue(on_shift(first, self.start+timedelta(days=44)))
        self.assertFalse(on_shift(first, self.start+timedelta(days=45)))
        self.assertTrue(on_shift(second, self.start+timedelta(days=45)))
        self.assertTrue(on_shift(first, self.start+timedelta(days=90)))
        days, rows = matrix(self.plan, self.start, self.end)
        self.assertEqual(set(rows[0]['present']), {1})
        self.assertEqual(set(rows[0]['shortage']), {0})
        self.assertEqual(generate_people(self.position), 0)

    def test_arbitrary_cycles_and_growing_demand_are_covered(self):
        self.position.on_days, self.position.off_days = 30, 60
        self.position.save()
        self.plan.demand[80]['count'] = 2
        self.plan.save()
        self.assertEqual(generate_people(self.position), 6)
        self.assertEqual(set(matrix(self.plan, self.start, self.end)[1][0]['present']), {2})
        self.assertIn(1, matrix(self.plan, self.start, self.end)[1][0]['excess'])

    def test_personal_schedule_and_explicit_default_application(self):
        generate_people(self.position)
        person = self.position.people.first()
        person.on_days = 15
        person.save()
        anchor = person.anchor
        response = self.client.post(reverse('rotation:role_update', args=[self.position.pk]), {'on_days':30,'off_days':30,'anchor':self.start})
        self.assertEqual(response.status_code, 302)
        person.refresh_from_db()
        self.assertEqual(person.on_days, 15)
        self.client.post(reverse('rotation:plan_detail', args=[self.plan.pk]), {'action':'apply','position':self.position.pk})
        person.refresh_from_db()
        self.assertEqual((person.on_days,person.off_days,person.anchor),(30,30,anchor))

    def test_missing_demand_is_distinct_from_zero_and_refresh_keeps_people(self):
        generate_people(self.position)
        self.plan.demand = [{'brigade_id':self.brigade.pk,'date':self.start.isoformat(),'count':0}]
        self.plan.save()
        row = matrix(self.plan,self.start,self.start+timedelta(days=1))[1][0]
        self.assertEqual(row['needed'], [0,None])
        self.assertEqual(row['shortage'], [0,None])
        refresh_demand(self.plan)
        self.assertEqual(self.position.people.count(), 2)

    def test_create_and_render_matrix_and_edit_delete_person(self):
        self.assertEqual(self.client.get(reverse('rotation:plan_list')).status_code,200)
        response = self.client.post(reverse('rotation:plan_create'), {'source':self.source.pk,'title':'Вторая','start':self.start,'end':self.end})
        self.assertEqual(response.status_code,302)
        self.client.post(reverse('rotation:plan_detail',args=[self.plan.pk]), {'action':'generate','position':self.position.pk})
        response = self.client.get(reverse('rotation:plan_detail',args=[self.plan.pk]), {'month':'2026-02'})
        self.assertContains(response,'Нехватка')
        self.assertEqual(len(response.context['days']),28)
        self.assertContains(response,'Работник 1')
        person = self.position.people.first()
        response = self.client.post(reverse('rotation:person_update',args=[person.pk]),{'name':'Водитель А','on_days':20,'off_days':20,'anchor':'2026-01-10'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.get(reverse('rotation:person_delete',args=[person.pk])).status_code,200)
        self.client.post(reverse('rotation:person_delete',args=[person.pk]))
        self.assertEqual(self.position.people.count(),1)

    def test_company_and_role_isolation(self):
        generate_people(self.position)
        person = self.position.people.first()
        self.client.force_login(self.other_user)
        for route,pk in [('plan_detail',self.plan.pk),('role_update',self.position.pk),('person_update',person.pk),('person_delete',person.pk)]:
            self.assertEqual(self.client.get(reverse('rotation:'+route,args=[pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('rotation:plan_list')),'Перевахта</a></td>')
        self.client.force_login(self.user)
        self.user.role = Role.objects.get(code='MANAGER');self.user.save(update_fields=['role'])
        self.assertEqual(self.client.get(reverse('rotation:plan_detail',args=[self.plan.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('rotation:plan_detail',args=[self.plan.pk]),{'action':'generate','position':self.position.pk}).status_code,403)
        self.assertEqual(self.client.get(reverse('rotation:plan_create')).status_code,403)

    def test_validation_bounds_company_and_bad_month(self):
        for on,off in [(0,45),(45,0),(366,45)]:
            person = RotationPerson(company=self.company,position=self.position,name='Invalid',on_days=on,off_days=off,anchor=self.start)
            with self.assertRaises(ValidationError): person.save()
        self.position.brigade=self.foreign_brigade
        with self.assertRaises(ValidationError): self.position.save()
        response = self.client.get(reverse('rotation:plan_detail',args=[self.plan.pk]),{'month':'bad'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.post(reverse('rotation:plan_detail',args=[self.plan.pk]),{'action':'generate','position':'bad'}).status_code,302)
