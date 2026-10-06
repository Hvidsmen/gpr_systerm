from django.test import TestCase
from django.urls import reverse
from apps.planning.models import LoadProfile
from apps.accounts.models import Company, Role, User
from apps.projects.models import Project, ConstructionObject, Section
from .models import ProjectWork, WorkGroup, MeasurementUnit
from .forms import ProjectWorkForm


class WorkCatalogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Catalogs')
        cls.profile = LoadProfile.objects.create(company=cls.company, code="required", name="Profile")
        cls.foreign = Company.objects.create(name='Foreign catalogs')
        cls.user = User.objects.create_user(username='catalog-planner',company=cls.company,role=Role.objects.get(code='PLANNER'))
        project = Project.objects.create(company=cls.company,code='p',name='p')
        cls.obj = ConstructionObject.objects.create(company=cls.company,project=project,code='o',name='o')
        cls.section = Section.objects.create(company=cls.company,construction_object=cls.obj,code='s',name='s')

    def setUp(self):
        self.client.force_login(self.user)

    def form(self, **values):
        data={'load_profile':self.profile.pk,'code':'w','name':'Work','section':self.section.pk,'unit':'м','unit_price':1,'kind':'SIMPLE','status':'PLANNED'}
        data.update(values)
        return ProjectWorkForm(data,user=self.user,fixed_object=self.obj,instance=ProjectWork(company=self.company))

    def test_default_units_seeded_and_group_optional(self):
        self.assertTrue(MeasurementUnit.objects.filter(company=self.company,symbol='м').exists())
        form=self.form()
        self.assertTrue(form.is_valid(),form.errors)
        work=form.save()
        self.assertIsNone(work.work_group)
        self.assertEqual(work.unit,'м')

    def test_quick_group_and_unit_creation_can_be_used_without_reloading(self):
        response=self.client.post(reverse('works:group_create'),{'name':'Земляные работы'})
        self.assertEqual(response.status_code,201)
        group=WorkGroup.objects.get(pk=response.json()['value'])
        self.assertEqual(group.company,self.company)
        response=self.client.post(reverse('works:unit_create'),{'symbol':'рейс','name':'Рейс'})
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json()['value'],'рейс')
        form=self.form(work_group=group.pk,unit=response.json()['value'])
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.save().work_group,group)

    def test_foreign_group_and_unit_and_free_text_unit_rejected(self):
        group=WorkGroup.objects.create(company=self.foreign,name='Foreign group')
        MeasurementUnit.objects.create(company=self.foreign,symbol='foreign-unit',name='Foreign unit')
        self.assertFalse(self.form(work_group=group.pk).is_valid())
        self.assertFalse(self.form(unit='foreign-unit').is_valid())
        self.assertFalse(self.form(unit='random-free-text').is_valid())
        response=self.client.get(reverse('works:group_list'))
        self.assertNotContains(response,'Foreign group')

    def test_duplicate_catalog_names_show_errors_and_different_company_allowed(self):
        for route,data,field in [('works:group_create',{'name':'Group'},'name'),('works:unit_create',{'symbol':'рейс','name':'Рейс'},'symbol')]:
            self.assertEqual(self.client.post(reverse(route),data).status_code,201)
            response=self.client.post(reverse(route),data)
            self.assertEqual(response.status_code,400)
            self.assertIn(field,response.json()['errors'])
        WorkGroup.objects.create(company=self.foreign,name='Group')

    def test_manager_can_read_but_not_create_and_foreman_cannot_edit_catalogs(self):
        for code in ('MANAGER','FOREMAN'):
            user=User.objects.create_user(username='catalog-'+code,company=self.company,role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('works:group_create'),{'name':'Denied'}).status_code,403)
            self.assertEqual(self.client.post(reverse('works:unit_create'),{'symbol':'denied','name':'Denied'}).status_code,403)
            self.assertEqual(self.client.get(reverse('works:group_list')).status_code,200 if code=='MANAGER' else 403)

    def test_form_contains_quick_create_without_nested_forms(self):
        response=self.client.get(reverse('works:work_create'))
        self.assertContains(response,'id_work_group')
        self.assertContains(response,'data-catalog-open="group"')
        self.assertContains(response,'data-catalog-open="unit"')
        self.assertContains(response,'catalog-group')
