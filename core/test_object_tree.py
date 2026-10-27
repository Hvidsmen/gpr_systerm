from django import forms
from django.template import engines
from django.test import TestCase
from apps.accounts.models import Company
from apps.projects.models import ConstructionObject, Project
from .object_tree import options_for


class ObjectTreeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Дерево')
        cls.project=Project.objects.create(company=cls.company,name='Проект')
        cls.root=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='КТП')
        cls.child=ConstructionObject.objects.create(company=cls.company,project=cls.project,parent=cls.root,name='КТП-1 Нефтепровод')
        cls.sibling=ConstructionObject.objects.create(company=cls.company,project=cls.project,parent=cls.root,name='КТП-2 Нефтепровод')
        cls.other=ConstructionObject.objects.create(company=cls.company,project=cls.project,name='Вертолетная площадка')

    def test_model_field_builds_one_preorder_tree_and_retains_selection(self):
        field=forms.ModelChoiceField(queryset=ConstructionObject.objects.filter(company=self.company).order_by('-name'))
        html=field.widget.render('object',self.child.pk)
        self.assertEqual(html.count(f'data-object-id="{self.root.pk}"'),1)
        self.assertLess(html.index('>КТП</option>'),html.index('КТП-1 Нефтепровод</option>'))
        self.assertLess(html.index('КТП-1 Нефтепровод</option>'),html.index('КТП-2 Нефтепровод</option>'))
        self.assertIn('data-object-depth="1"',html)
        self.assertIn('data-object-path="КТП → КТП-1 Нефтепровод"',html)
        self.assertIn(f'value="{self.child.pk}" selected',html)
        self.assertIsNone(field.clean(str(self.child.pk)).parent.parent_id)

    def test_scoped_child_has_disabled_ancestor_without_exposing_siblings(self):
        field=forms.ModelChoiceField(queryset=ConstructionObject.objects.filter(pk=self.child.pk))
        html=field.widget.render('object',None)
        self.assertIn('disabled data-object-id=',html)
        self.assertIn('data-object-context="true"',html)
        self.assertNotIn(self.sibling.name,html)
        self.assertNotIn(self.other.name,html)
        with self.assertRaises(forms.ValidationError):field.clean(self.root.pk)

    def test_manual_filters_use_same_options_and_safe_escaping(self):
        self.child.name='<КТП & 1>';self.child.save()
        template=engines['django'].from_string('{% load object_tree %}<select>{% object_options objects selected %}</select>')
        html=template.render({'objects':ConstructionObject.objects.filter(pk=self.child.pk),'selected':str(self.child.pk)})
        self.assertIn('&lt;КТП &amp; 1&gt;',html)
        self.assertIn(f'value="{self.child.pk}" selected',html)
        self.assertIn('disabled',html)
        self.assertNotIn('Макрообъект',html)

    def test_nonobject_select_is_unchanged_and_tree_uses_one_catalogue_query(self):
        field=forms.ChoiceField(choices=[('a','Один'),('b','Два')])
        self.assertNotIn('data-object',field.widget.render('plain','b'))
        objects=list(ConstructionObject.objects.filter(company=self.company))
        with self.assertNumQueries(1):options=options_for(objects,self.child.pk)
        self.assertEqual(len(options),4)
