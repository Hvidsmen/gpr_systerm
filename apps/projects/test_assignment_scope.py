from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from .models import Project,ConstructionObject,Section


class ObjectCatalogScopeTests(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Catalog scope')
        self.project=Project.objects.create(company=self.company,name='Shared project')
        self.allowed=ConstructionObject.objects.create(company=self.company,project=self.project,name='Assigned KTP')
        self.hidden=ConstructionObject.objects.create(company=self.company,project=self.project,name='Hidden bridges')
        self.section=Section.objects.create(company=self.company,construction_object=self.hidden,name='Hidden section')
        self.planner=User.objects.create_user(username='catalog-planner',company=self.company,role=Role.objects.get(code='PLANNER'))
        self.admin=User.objects.create_user(username='catalog-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.planner.assigned_objects.add(self.allowed)
        self.client.force_login(self.planner)

    def test_catalog_and_project_card_only_show_assigned_objects(self):
        for name in ['object_list','project_detail']:
            response=self.client.get(reverse('projects:'+name,args=[self.project.pk]))
            self.assertEqual(response.status_code,200)
            self.assertContains(response,'Assigned KTP');self.assertNotContains(response,'Hidden bridges')

    def test_hidden_object_actions_and_sections_blocked(self):
        for name in ['object_edit','object_delete','section_list','section_create']:
            url=reverse('projects:'+name,args=[self.hidden.pk])
            self.assertEqual(self.client.get(url).status_code,404,name)
            self.assertEqual(self.client.post(url,{}).status_code,404,name)
        for name in ['section_edit','section_delete']:
            self.assertEqual(self.client.post(reverse('projects:'+name,args=[self.hidden.pk,self.section.pk]),{}).status_code,404)
        self.assertEqual(self.client.post(reverse('projects:project_delete',args=[self.project.pk]),{}).status_code,404)
        self.assertTrue(ConstructionObject.objects.filter(pk=self.hidden.pk).exists())

    def test_admin_impersonation_and_return(self):
        self.client.force_login(self.admin)
        url=reverse('projects:object_list',args=[self.project.pk])
        self.assertContains(self.client.get(url),'Hidden bridges')
        self.client.post(reverse('accounts:impersonation_start',args=[self.planner.pk]))
        self.assertNotContains(self.client.get(url),'Hidden bridges')
        self.client.post(reverse('accounts:impersonation_stop'))
        self.assertContains(self.client.get(url),'Hidden bridges')

    def test_no_assignments_cannot_open_project_catalog(self):
        self.planner.assigned_objects.clear()
        self.assertEqual(self.client.get(reverse('projects:object_list',args=[self.project.pk])).status_code,404)

    def test_work_catalog_and_filters_and_actions_follow_assignments(self):
        from apps.works.models import ProjectWork,ProjectWorkItem
        from apps.works.batch_prepare import SelectionForm, batch_object
        from django.core.exceptions import ValidationError
        section=Section.objects.create(company=self.company,construction_object=self.allowed,name='Assigned section')
        good=ProjectWork.objects.create(company=self.company,section=section,name='Allowed work',unit='m',kind='COMPOSITE')
        hidden=ProjectWork.objects.create(company=self.company,section=self.section,name='Hidden work',unit='m',kind='COMPOSITE')
        response=self.client.get(reverse('works:work_list'))
        self.assertContains(response,'Allowed work');self.assertNotContains(response,'Hidden work')
        self.assertNotContains(response,'Hidden bridges');self.assertNotContains(response,'Hidden section')
        choices=response.context['filter_form'].fields['construction_object'].queryset
        self.assertEqual(list(choices),[self.allowed])
        response=self.client.get(reverse('works:work_list'),{'construction_object':self.hidden.pk})
        self.assertTrue(response.context['filter_form'].errors)
        for name, args in [('work_detail',[hidden.pk]),('work_update',[hidden.pk]),('work_delete',[hidden.pk]),('price_change',[hidden.pk]),('work_item_create',[hidden.pk])]:
            url=reverse('works:'+name,args=args)
            self.assertEqual(self.client.get(url).status_code,404,name)
            self.assertEqual(self.client.post(url,{}).status_code,405 if name == 'work_detail' else 404,name)
        self.assertEqual(list(SelectionForm(user=self.planner).fields['construction_object'].queryset),[self.allowed])
        with self.assertRaises(ValidationError):batch_object(self.planner,self.hidden.pk)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('works:work_list')),'Hidden work')
