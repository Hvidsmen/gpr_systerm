from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import Role,User
from . import test_schedule_import as fixtures
from .schedule_import import apply_rows
from .models import ProjectWork


class ComplexObjectDetailTests(TestCase):
    def setUp(self):
        fixtures.ScheduleImportTests.setUp(self)
        apply_rows(self.user,self.section,self.profile,self.rows)
        self.work=ProjectWork.objects.get(section=self.section)
        self.facility=self.work.complex_object
        self.url=reverse('works:complex_object_detail',args=[self.facility.pk])

    def test_detail_shows_full_tree_and_actions(self):
        response=self.client.get(self.url)
        self.assertContains(response,self.facility.name)
        self.assertContains(response,self.work.name)
        for item in self.work.items.all():self.assertContains(response,item.name)
        self.assertContains(response,'Удалить комплексный объект целиком')
        self.assertContains(response,'Изменить название и описание')
        self.assertContains(response,'До комплексных объектов')

    def test_edit_name_preserves_work_and_composition(self):
        response=self.client.post(self.url,{'name':'Новое имя','description':'Описание','section':self.section.pk})
        self.assertRedirects(response,self.url)
        self.facility.refresh_from_db();self.work.refresh_from_db()
        self.assertEqual(self.facility.name,'Новое имя')
        self.assertEqual(self.work.complex_object_id,self.facility.pk)
        self.assertEqual(self.work.items.count(),2)

    def test_readonly_and_unassigned_scope(self):
        user=User.objects.create_user(username='object-observer',company=self.company,role=Role.objects.get(code='OBSERVER'))
        self.client.force_login(user)
        response=self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'Удалить комплексный объект целиком')
        self.assertEqual(self.client.post(self.url,{'name':'Запрет'}).status_code,403)
        user.role=Role.objects.get(code='PLANNER');user.save()
        self.assertEqual(self.client.get(self.url).status_code,404)

    def test_table_tree_switch_preserves_preference_and_composition(self):
        response=self.client.get(self.url,{'view':'table'})
        self.assertEqual(response.context['view_mode'],'table')
        self.assertContains(response,'Доля от суммы, %')
        self.assertNotContains(response,'data-tree-expand')
        for item in self.work.items.all():self.assertContains(response,item.name)
        self.assertEqual(self.client.get(self.url).context['view_mode'],'table')
        response=self.client.get(self.url,{'view':'tree'})
        self.assertContains(response,'data-tree-expand')
        self.assertEqual(self.client.get(self.url).context['view_mode'],'tree')

    def test_package_and_item_filters_are_scoped_and_preserved_between_views(self):
        other=ProjectWork.objects.create(company=self.company,section=self.section,complex_object=self.facility,name='Другой комплекс',kind='COMPOSITE')
        item=self.work.items.first()
        response=self.client.get(self.url,{'package':self.work.pk,'item':item.pk,'view':'table'})
        self.assertEqual([work.pk for work in response.context['works']],[self.work.pk])
        self.assertEqual([row.pk for row in response.context['works'][0].catalogue_items],[item.pk])
        self.assertContains(response,f'package={self.work.pk}&amp;item={item.pk}&amp;view=tree')
        response=self.client.get(self.url,{'package':other.pk,'item':item.pk})
        self.assertFalse(response.context['works'])
        self.assertIn('item',response.context['filter_form'].errors)
        response=self.client.get(self.url,{'package':'invalid'})
        self.assertFalse(response.context['works'])

    def test_change_section_in_card_moves_the_composition(self):
        from apps.projects.models import Section
        target=Section.objects.create(company=self.company,construction_object=self.obj,name='Новый раздел')
        response=self.client.post(self.url,{'name':self.facility.name,'description':'','section':target.pk})
        self.assertRedirects(response,self.url)
        self.facility.refresh_from_db();self.work.refresh_from_db()
        self.assertEqual(self.facility.section_id,target.pk)
        self.assertEqual(self.work.section_id,target.pk)
