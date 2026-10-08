from datetime import timedelta
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role
from apps.projects.models import Project, ConstructionObject
from apps.planning.models import GlobalPlanVersion
from apps.resources.models import Brigade
from .tests import RotationTests
from .models import RotationPlan, RotationRole, RotationPerson, RotationStatus
from .services import generate_people, matrix


class StatusResetTests(TestCase):
    setUpTestData = classmethod(RotationTests.setUpTestData.__func__)

    def setUp(self):
        RotationTests.setUp(self)
        generate_people(self.position)
        self.person = self.position.people.first()
        self.first = self.status(self.person, self.start)
        self.later = self.status(self.person, self.start+timedelta(days=50))
        other_brigade = Brigade.objects.create(company=self.company, name='Сварщик')
        other_role = RotationRole.objects.create(company=self.company, plan=self.plan, brigade=other_brigade, anchor=self.start)
        other_person = RotationPerson.objects.create(company=self.company, position=other_role, name='Сварщик №1', anchor=self.start)
        self.other_role_status = self.status(other_person,self.start)
        second = RotationPlan.objects.create(company=self.company, source=self.source, title='Второй план объекта', start=self.start, end=self.end)
        self.second_plan_status = self.seed_plan(second, self.brigade)
        other_obj = ConstructionObject.objects.create(company=self.company, project=self.project, name='Другой объект')
        self.other_obj_status = self.seed_object(self.company, other_obj, self.brigade)
        foreign_project = Project.objects.create(company=self.foreign, name='Чужой проект')
        foreign_obj = ConstructionObject.objects.create(company=self.foreign, project=foreign_project, name='Чужой объект')
        self.foreign_status = self.seed_object(self.foreign,foreign_obj,self.foreign_brigade)

    def status(self, person, day):
        return RotationStatus.objects.create(company=person.company, person=person, day=day, status='OFF')

    def seed_plan(self, plan, brigade):
        position = RotationRole.objects.create(company=plan.company,plan=plan,brigade=brigade,anchor=self.start)
        person = RotationPerson.objects.create(company=plan.company,position=position,name='Человек №1',anchor=self.start)
        return self.status(person,self.start)

    def seed_object(self, company, obj, brigade):
        source=GlobalPlanVersion.objects.create(company=company, construction_object=obj,version_number=1,start_date=self.start,end_date=self.end)
        plan=RotationPlan.objects.create(company=company,source=source,title='План',start=self.start,end=self.end)
        return self.seed_plan(plan,brigade)

    def url(self,scope,pk):
        return reverse('rotation:status_reset',args=[scope,pk])

    def test_get_is_preview_and_post_requires_confirmation(self):
        url=self.url('role',self.position.pk)
        response=self.client.get(url,{'month':'2026-02'})
        self.assertEqual(response.context['count'],2)
        self.assertContains(response,'ко всему периоду')
        self.assertEqual(RotationStatus.objects.count(),6)
        response=self.client.post(url,{})
        self.assertEqual(response.status_code,200)
        self.assertIn('confirm',response.context['form'].errors)
        self.assertEqual(RotationStatus.objects.count(),6)

    def test_role_reset_includes_all_months_preserves_graphs_and_other_roles(self):
        graphs=list(RotationPerson.objects.values_list('pk','name','on_days','off_days','anchor'))
        response=self.client.post(self.url('role',self.position.pk),{'confirm':'on','month':'2026-02'})
        self.assertEqual(response.status_code,302)
        self.assertIn('month=2026-02',response.url)
        self.assertFalse(RotationStatus.objects.filter(pk__in=[self.first.pk,self.later.pk]).exists())
        self.assertTrue(RotationStatus.objects.filter(pk=self.other_role_status.pk).exists())
        self.assertEqual(list(RotationPerson.objects.values_list('pk','name','on_days','off_days','anchor')),graphs)
        row=next(row for row in matrix(self.plan,self.start,self.start)[1] if row['position'].pk==self.position.pk)
        self.assertEqual(row['present'],[1])

    def test_plan_reset_does_not_touch_another_plan_on_same_object(self):
        self.client.post(self.url('plan',self.plan.pk),{'confirm':'on'})
        self.assertFalse(RotationStatus.objects.filter(person__position__plan=self.plan).exists())
        self.assertTrue(RotationStatus.objects.filter(pk=self.second_plan_status.pk).exists())
        self.assertEqual(RotationStatus.objects.count(),3)

    def test_object_reset_touches_all_its_plans_and_no_other_object(self):
        response=self.client.get(self.url('object',self.obj.pk))
        self.assertEqual(response.context['count'],4)
        self.assertEqual(response.context['plan_count'],2)
        self.client.post(self.url('object',self.obj.pk),{'confirm':'on'})
        self.assertEqual(set(RotationStatus.objects.values_list('pk',flat=True)),{self.other_obj_status.pk,self.foreign_status.pk})

    def test_all_reset_is_limited_to_own_company_and_repeat_is_safe(self):
        url=self.url('all',0)
        self.client.post(url,{'confirm':'on'})
        self.assertEqual(list(RotationStatus.objects.values_list('pk',flat=True)),[self.foreign_status.pk])
        self.assertEqual(self.client.post(url,{'confirm':'on'}).status_code,302)
        self.assertContains(self.client.get(url),'ручных статусов нет')

    def test_company_isolation_invalid_scope_and_reader_permissions(self):
        self.client.force_login(self.other_user)
        for scope,pk in [('plan',self.plan.pk),('role',self.position.pk),('object',self.obj.pk)]:
            self.assertEqual(self.client.post(self.url(scope,pk),{'confirm':'on'}).status_code,404)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url('bad',0)).status_code,404)
        self.assertEqual(self.client.get(self.url('all',self.plan.pk)).status_code,404)
        self.user.role=Role.objects.get(code='MANAGER');self.user.save(update_fields=['role'])
        self.assertEqual(self.client.get(self.url('all',0)).status_code,403)
        self.assertEqual(self.client.post(self.url('plan',self.plan.pk),{'confirm':'on'}).status_code,403)
        self.assertEqual(RotationStatus.objects.count(),6)
