from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, Role, User
from apps.projects.models import Project, ConstructionObject, Section
from .models import ProjectWork
from .forms import ProjectWorkForm


class WorkLocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name='Work location')
        cls.user = User.objects.create_user(username='location-planner', company=cls.company, role=Role.objects.get(code='PLANNER'))
        cls.projects, cls.objects, cls.sections = [], [], []
        for i in range(2):
            project = Project.objects.create(company=cls.company, code=str(i), name='Project '+str(i))
            obj = ConstructionObject.objects.create(company=cls.company, project=project, code='o', name='Object')
            section = Section.objects.create(company=cls.company, construction_object=obj, code='s', name='Repeated section name')
            cls.projects.append(project); cls.objects.append(obj); cls.sections.append(section)
        cls.foreign = Company.objects.create(name='Foreign')
        cls.foreign_project = Project.objects.create(company=cls.foreign, code='p', name='Foreign project')
        cls.foreign_object = ConstructionObject.objects.create(company=cls.foreign, project=cls.foreign_project, code='o', name='Foreign object')
        cls.foreign_section = Section.objects.create(company=cls.foreign, construction_object=cls.foreign_object, code='s', name='Foreign section')

    def setUp(self):
        self.client.force_login(self.user)

    def data(self):
        return {'project':self.projects[0].pk, 'construction_object':self.objects[0].pk, 'section':self.sections[0].pk,
                'code':'new', 'name':'Work', 'unit':'m', 'unit_price':'10', 'status':'PLANNED', 'kind':'SIMPLE', 'allow_fractional':'on'}

    def test_empty_form_starts_with_project_and_scopes_json_to_company(self):
        response=self.client.get(reverse('works:work_create'))
        self.assertEqual(response.status_code,200)
        form=response.context['form']
        self.assertEqual(list(form.fields)[:3],['project','construction_object','section'])
        self.assertFalse(form.fields['construction_object'].queryset.exists())
        self.assertFalse(form.fields['section'].queryset.exists())
        self.assertEqual({row['id'] for row in response.context['work_hierarchy']['objects']},{obj.pk for obj in self.objects})
        self.assertNotContains(response,'Foreign project')
        self.assertNotContains(response,'Foreign section')

    def test_valid_chain_creates_work(self):
        response=self.client.post(reverse('works:work_create'), self.data())
        self.assertEqual(response.status_code,302)
        work=ProjectWork.objects.get()
        self.assertEqual(work.section,self.sections[0])
        self.assertEqual(work.company,self.company)

    def test_object_from_other_project_and_section_from_other_object_are_rejected(self):
        for field, value in [('construction_object',self.objects[1].pk),('section',self.sections[1].pk)]:
            response=self.client.post(reverse('works:work_create'),{**self.data(),field:value})
            self.assertEqual(response.status_code,200)
            self.assertIn(field,response.context['form'].errors)
        self.assertFalse(ProjectWork.objects.exists())

    def test_foreign_chain_is_rejected(self):
        response=self.client.post(reverse('works:work_create'),{**self.data(),'project':self.foreign_project.pk,'construction_object':self.foreign_object.pk,'section':self.foreign_section.pk})
        self.assertEqual(response.status_code,200)
        for field in ('project','construction_object','section'):
            self.assertIn(field,response.context['form'].errors)
        self.assertFalse(ProjectWork.objects.exists())

    def test_edit_prefills_chain_and_invalid_post_preserves_selected_location(self):
        work=ProjectWork.objects.create(company=self.company,section=self.sections[0],code='old',name='Old',unit='m',unit_price=10)
        url=reverse('works:work_update',args=[work.pk])
        response=self.client.get(url)
        form=response.context['form']
        self.assertEqual(form['project'].value(),self.projects[0].pk)
        self.assertEqual(form['construction_object'].value(),self.objects[0].pk)
        self.assertEqual(list(form.fields['section'].queryset),[self.sections[0]])
        response=self.client.post(url,{**self.data(),'name':''})
        self.assertIn('name',response.context['form'].errors)
        self.assertEqual(int(response.context['form']['section'].value()),self.sections[0].pk)
        self.assertEqual(self.client.post(url,self.data()).status_code,302)

    def test_workspace_fixed_object_does_not_depend_on_posted_location(self):
        form=ProjectWorkForm({**self.data(),'project':self.foreign_project.pk,'construction_object':self.foreign_object.pk},
                             user=self.user,fixed_object=self.objects[0],instance=ProjectWork(company=self.company))
        self.assertTrue(form.is_valid(),form.errors)
        self.assertEqual(form.cleaned_data['project'],self.projects[0])
        self.assertEqual(form.cleaned_data['construction_object'],self.objects[0])
