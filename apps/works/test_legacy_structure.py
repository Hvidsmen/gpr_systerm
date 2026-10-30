from datetime import date
from io import BytesIO
from django.contrib import admin
from django.test import TestCase,Client,RequestFactory
from django.urls import reverse
from apps.accounts.models import Company,Role,User
from apps.projects.models import Project,ConstructionObject,Section
from apps.planning.models import LoadProfile
from apps.production.models import DailyFact
from .models import ProjectWork,ProjectWorkItem,MeasurementUnit
from .forms import ProjectWorkForm,ProjectWorkItemForm
from .admin import ProjectWorkAdmin,ProjectWorkItemAdmin


class LegacyStructureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Legacy work metadata')
        cls.project=Project.objects.create(company=cls.company,name='Проект')
        cls.obj=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='КТП')
        cls.section=Section.objects.create(company=cls.company,construction_object=cls.obj,name='Раздел')
        cls.profile=LoadProfile.objects.create(company=cls.company,name='Равномерный')
        MeasurementUnit.objects.get_or_create(company=cls.company,symbol='м',defaults={'name':'метр'})
        cls.simple=ProjectWork.objects.create(company=cls.company,section=cls.section,name='Самостоятельная работа',unit='м',unit_price=10,load_profile=cls.profile)
        cls.package=ProjectWork.objects.create(company=cls.company,section=cls.section,name='Комплекс',kind='COMPOSITE',unit='м',unit_price=50)
        cls.item=ProjectWorkItem.objects.create(company=cls.company,project_work=cls.package,name='Работа в комплексе',unit='м',load_profile=cls.profile,quantity_per_unit=1,weight=100)
        cls.admin=User.objects.create_user(username='legacy-admin',company=cls.company,role=Role.objects.get(code='ADMIN'))
        cls.planner=User.objects.create_user(username='legacy-planner',company=cls.company,role=Role.objects.get(code='PLANNER'))
        cls.observer=User.objects.create_user(username='legacy-observer',company=cls.company,role=Role.objects.get(code='OBSERVER'))
        cls.planner.assigned_objects.add(cls.obj)
        cls.other=Company.objects.create(name='Foreign legacy company')
        project=Project.objects.create(company=cls.other,name='Other')
        obj=ConstructionObject.objects.create(company=cls.other,project=project,name='Other')
        section=Section.objects.create(company=cls.other,construction_object=obj,name='Other')
        cls.foreign=ProjectWork.objects.create(company=cls.other,section=section,name='Secret',kind='COMPOSITE')

    def data(self,**extra):
        return {'name':self.simple.name,'project':self.project.pk,'construction_object':self.obj.pk,'section':self.section.pk,
            'kind':'SIMPLE','unit':'м','unit_price':'10','status':'PLANNED','load_profile':self.profile.pk,'allow_fractional':'on',**extra}

    def item_data(self,**extra):
        return {'name':self.item.name,'unit':'м','load_profile':self.profile.pk,'quantity_per_unit':'1','weight':'100',**extra}

    def mark(self):
        ProjectWork.objects.filter(pk=self.simple.pk).update(is_legacy_structure=True)
        ProjectWorkItem.objects.filter(pk=self.item.pk).update(is_legacy_structure=True)
        self.simple.refresh_from_db();self.item.refresh_from_db()

    def test_admin_can_set_clear_and_create_metadata_in_work_and_component_forms(self):
        self.client.force_login(self.admin)
        for url,data,model,pk in [(reverse('works:work_update',args=[self.simple.pk]),self.data(),ProjectWork,self.simple.pk),
                (reverse('works:work_item_update',args=[self.item.pk]),self.item_data(),ProjectWorkItem,self.item.pk)]:
            response=self.client.post(url,{**data,'is_legacy_structure':'on'});self.assertEqual(response.status_code,302)
            self.assertTrue(model.objects.get(pk=pk).is_legacy_structure)
            self.assertEqual(self.client.post(url,data).status_code,302);self.assertFalse(model.objects.get(pk=pk).is_legacy_structure)
        response=self.client.post(reverse('works:work_create'),self.data(name='Новая старая работа',is_legacy_structure='on'))
        self.assertEqual(response.status_code,302);self.assertTrue(ProjectWork.objects.get(name='Новая старая работа').is_legacy_structure)

    def test_planner_cannot_set_or_clear_marker_by_forged_post(self):
        self.client.force_login(self.planner)
        for marked in [False,True]:
            ProjectWork.objects.filter(pk=self.simple.pk).update(is_legacy_structure=marked)
            ProjectWorkItem.objects.filter(pk=self.item.pk).update(is_legacy_structure=marked)
            for url,data,model,pk in [(reverse('works:work_update',args=[self.simple.pk]),self.data(is_legacy_structure='' if marked else 'on'),ProjectWork,self.simple.pk),
                    (reverse('works:work_item_update',args=[self.item.pk]),self.item_data(is_legacy_structure='' if marked else 'on'),ProjectWorkItem,self.item.pk)]:
                self.assertEqual(self.client.post(url,data).status_code,302)
                self.assertEqual(model.objects.get(pk=pk).is_legacy_structure,marked)
        response=self.client.post(reverse('works:work_create'),self.data(name='Попытка пометки',is_legacy_structure='on'))
        self.assertEqual(response.status_code,302);self.assertFalse(ProjectWork.objects.get(name='Попытка пометки').is_legacy_structure)

    def test_stale_normal_edit_keeps_new_admin_marker(self):
        work_form=ProjectWorkForm(self.data(name='Уточнено'),instance=ProjectWork.objects.get(pk=self.simple.pk),user=self.planner)
        item_form=ProjectWorkItemForm(self.item_data(name='Уточнено в комплексе'),instance=ProjectWorkItem.objects.get(pk=self.item.pk),user=self.planner)
        self.assertTrue(work_form.is_valid(),work_form.errors);self.assertTrue(item_form.is_valid(),item_form.errors)
        self.mark()
        work_form.save();item_form.save()
        self.assertTrue(ProjectWork.objects.get(pk=self.simple.pk).is_legacy_structure)
        self.assertTrue(ProjectWorkItem.objects.get(pk=self.item.pk).is_legacy_structure)

    def test_badges_fields_and_filter_are_admin_only(self):
        self.mark()
        for user,visible in [(self.admin,True),(self.planner,False),(self.observer,False)]:
            self.client.force_login(user)
            for name,args in [('work_list',[]),('work_detail',[self.simple.pk]),('work_detail',[self.package.pk])]:
                response=self.client.get(reverse('works:'+name,args=args));self.assertEqual(response.status_code,200)
                (self.assertContains if visible and name!='work_list' else self.assertNotContains)(response,'data-legacy-structure')
            if user!=self.observer:
                for name,args in [('work_update',[self.simple.pk]),('work_item_update',[self.item.pk])]:
                    response=self.client.get(reverse('works:'+name,args=args));self.assertEqual(response.status_code,200)
                    self.assertEqual('is_legacy_structure' in response.context['form'].fields,visible)
            response=self.client.get(reverse('works:work_list'),{'legacy_only':'on'})
            self.assertEqual('legacy_only' in response.context['filter_form'].fields,visible)
            self.assertEqual(set(w.pk for w in response.context['works']),{self.simple.pk,self.package.pk} if visible else {self.package.pk})

    def test_admin_filter_selects_only_old_components_default_selects_current(self):
        ProjectWorkItem.objects.filter(pk=self.item.pk).update(is_legacy_structure=True)
        self.client.force_login(self.admin)
        response=self.client.get(reverse('works:work_list'),{'legacy_only':'on'})
        self.assertEqual([w.pk for w in response.context['works']],[self.package.pk])
        self.client.force_login(self.planner)
        response=self.client.get(reverse('works:work_list'),{'legacy_only':'on'})
        self.assertEqual(set(w.pk for w in response.context['works']),{self.simple.pk,self.package.pk})

    def test_bulk_marker_is_company_scoped_audited_and_keeps_fact(self):
        from apps.audit.models import AuditLog
        fact=DailyFact.objects.create(company=self.company,project_work=self.simple,date=date(2026,10,1),actual_quantity=3)
        self.client.force_login(self.admin);url=reverse('works:legacy_structure_bulk')
        response=self.client.post(url,{'selected':[self.simple.pk,self.package.pk],'legacy_structure':'set'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(ProjectWork.objects.filter(company=self.company,is_legacy_structure=True).count(),2)
        fact.refresh_from_db();self.assertEqual(fact.actual_quantity,3)
        self.assertEqual(AuditLog.objects.filter(comment='Массовая отметка «Старая структура»').count(),1)
        self.assertEqual(self.client.post(url,{'selected':[self.simple.pk],'legacy_structure':'clear'}).status_code,302)
        self.assertFalse(ProjectWork.objects.get(pk=self.simple.pk).is_legacy_structure)

    def test_bulk_marker_rejects_other_roles_foreign_ids_empty_and_unsafe_payloads(self):
        url=reverse('works:legacy_structure_bulk')
        for user in [self.planner,self.observer]:
            self.client.force_login(user);self.assertEqual(self.client.post(url,{'selected':[self.simple.pk],'legacy_structure':'set'}).status_code,403)
        self.client.force_login(self.admin)
        for values in [{'selected':[self.simple.pk,self.foreign.pk],'legacy_structure':'set'},{'selected':['bad'],'legacy_structure':'set'},
                {'selected':[],'legacy_structure':'set'},{'selected':[self.simple.pk],'legacy_structure':'bad'}]:
            self.assertEqual(self.client.post(url,values).status_code,400)
            self.assertFalse(ProjectWork.objects.get(pk=self.simple.pk).is_legacy_structure)
        csrf=Client(enforce_csrf_checks=True);csrf.force_login(self.admin)
        self.assertEqual(csrf.post(url,{'selected':[self.simple.pk],'legacy_structure':'set'}).status_code,403)

    def test_excel_metadata_is_exposed_only_to_admin(self):
        import openpyxl
        self.mark()
        for user,visible in [(self.admin,True),(self.planner,False)]:
            self.client.force_login(user)
            response=self.client.get(reverse('works:work_list'),{'export':'xlsx'});self.assertEqual(response.status_code,200)
            workbook=openpyxl.load_workbook(BytesIO(response.content))
            for sheet in workbook:
                self.assertEqual('Старая структура' in [c.value for c in sheet[1]],visible)

    def test_django_admin_hides_metadata_from_non_admin_staff(self):
        request=RequestFactory().get('/admin/')
        for model,admin_class,obj in [(ProjectWork,ProjectWorkAdmin,self.simple),(ProjectWorkItem,ProjectWorkItemAdmin,self.item)]:
            model_admin=admin_class(model,admin.site)
            request.user=self.planner
            self.assertFalse(model_admin.lookup_allowed('is_legacy_structure__exact','1',request))
            self.assertIn('is_legacy_structure',model_admin.get_exclude(request,obj))
            self.assertNotIn('is_legacy_structure',model_admin.get_list_display(request))
            request.user=self.admin
            self.assertTrue(model_admin.lookup_allowed('is_legacy_structure__exact','1',request))
            self.assertNotIn('is_legacy_structure',model_admin.get_exclude(request,obj))
            self.assertIn('is_legacy_structure',model_admin.get_list_display(request))
