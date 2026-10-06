from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company, User, Role
from apps.projects.models import Project, ConstructionObject, Section
from apps.planning.models import LoadProfile
from .models import ProjectWork, ProjectWorkItem, WorkTemplate, WorkTemplateVersion, WorkTemplateItem
from .forms import ProjectWorkForm
from .services import WorkItemGeneratorService


class RequiredProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Required profiles')
        cls.user=User.objects.create_user(username='profile-planner',company=cls.company,role=Role.objects.get(code='PLANNER'))
        project=Project.objects.create(company=cls.company,code='p',name='p')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=project,code='o',name='o')
        cls.section=Section.objects.create(company=cls.company,construction_object=cls.obj,code='s',name='s')
        cls.profile=LoadProfile.objects.create(company=cls.company,code='p',name='Profile')
        cls.work=ProjectWork.objects.create(company=cls.company,section=cls.section,code='c',name='Composite',unit='м',kind='COMPOSITE')

    def setUp(self):
        self.client.force_login(self.user)

    def work_form(self, **values):
        data={'code':'new','name':'Work','unit':'м','unit_price':1,'section':self.section.pk,'kind':'SIMPLE','status':'PLANNED'}
        data.update(values)
        return ProjectWorkForm(data,user=self.user,fixed_object=self.obj,instance=ProjectWork(company=self.company))

    def test_simple_requires_profile_but_composite_does_not(self):
        form=self.work_form()
        self.assertFalse(form.is_valid())
        self.assertIn('load_profile',form.errors)
        form=self.work_form(load_profile=self.profile.pk)
        self.assertTrue(form.is_valid(),form.errors)
        self.assertTrue(self.work_form(kind='COMPOSITE').is_valid())

    def test_subwork_creation_requires_profile_and_preserves_input(self):
        url=reverse('works:work_item_create',args=[self.work.pk])
        data={'name':'Part','unit':'м','weight':100,'quantity_per_unit':1}
        response=self.client.post(url,data)
        self.assertEqual(response.status_code,200)
        self.assertIn('load_profile',response.context['form'].errors)
        self.assertEqual(response.context['form']['name'].value(),'Part')
        self.assertFalse(self.work.items.exists())
        self.assertEqual(self.client.post(url,{**data,'load_profile':self.profile.pk}).status_code,302)
        self.assertEqual(self.work.items.get().load_profile,self.profile)

    def test_edit_cannot_clear_subwork_profile(self):
        item=ProjectWorkItem.objects.create(company=self.company,project_work=self.work,name='Part',unit='м',weight=100,quantity_per_unit=1,load_profile=self.profile)
        response=self.client.post(reverse('works:work_item_update',args=[item.pk]),{'name':'Part','unit':'м','weight':100,'quantity_per_unit':1,'load_profile':''})
        self.assertIn('load_profile',response.context['form'].errors)
        item.refresh_from_db()
        self.assertEqual(item.load_profile,self.profile)

    def test_model_validation_requires_profile_for_simple_work_and_subwork(self):
        work=ProjectWork(company=self.company,section=self.section,code='s',name='Simple',unit='м')
        with self.assertRaises(ValidationError): work.full_clean()
        item=ProjectWorkItem(company=self.company,project_work=self.work,name='Part',unit='м',weight=100,quantity_per_unit=1)
        with self.assertRaises(ValidationError): item.full_clean()

    def test_template_without_profiles_is_rejected_and_old_subworks_preserved(self):
        template=WorkTemplate.objects.create(company=self.company,code='t',name='Template',unit='м')
        version=WorkTemplateVersion.objects.create(company=self.company,template=template,version_number=1,is_current=True)
        WorkTemplateItem.objects.create(company=self.company,version=version,name='Unconfigured',unit='м',weight=100,quantity_per_unit=1)
        form=self.work_form(kind='COMPOSITE',template=template.pk)
        self.assertFalse(form.is_valid())
        self.assertIn('template',form.errors)
        self.work.template=template;self.work.save()
        old=ProjectWorkItem.objects.create(company=self.company,project_work=self.work,name='Old',unit='м',load_profile=self.profile)
        with self.assertRaises(ValidationError): WorkItemGeneratorService.generate_from_template(self.work)
        self.assertTrue(ProjectWorkItem.objects.filter(pk=old.pk).exists())
