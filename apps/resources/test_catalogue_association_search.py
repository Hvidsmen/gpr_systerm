from io import BytesIO
from django import forms
from django.forms.models import ModelChoiceIteratorValue
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from apps.accounts.models import Company, Role, User
from .models import Brigade, EquipmentType, EquipmentCategory, BrigadeGroup, Position
from .forms import BrigadeForm, EquipmentTypeForm
from .catalogue_search import filter_name, alias_options


class CatalogueAssociationSearchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name='Association search')
        cls.user=User.objects.create_user(username='association-planner',company=cls.company,role=Role.objects.get(code='PLANNER'))
        cls.group=BrigadeGroup.objects.create(company=cls.company,name='ИТР')
        cls.category=EquipmentCategory.objects.create(company=cls.company,name='Строительная техника')
        cls.job=Brigade.objects.create(company=cls.company,name='Мастер участка',staffing_current=True,group=cls.group,
            excel_aliases=['Руководитель СМР','Прораб монтажного участка'])
        cls.old=Brigade.objects.create(company=cls.company,name='Старая должность',staffing_current=False,excel_aliases=['Руководитель СМР'])
        cls.root=EquipmentType.objects.create(company=cls.company,name='Экскаватор гусеничный',category=cls.category,
            excel_aliases=['ЭО-4225','ЭКСКАВАТОР на гусеницах','Техника [особая]'])
        cls.model=EquipmentType.objects.create(company=cls.company,name='CAT 320',parent=cls.root,excel_aliases=['Катерпиллер 320'])
        cls.foreign=Company.objects.create(name='Foreign association search')
        Brigade.objects.create(company=cls.foreign,name='Чужой мастер',staffing_current=True,excel_aliases=['Руководитель СМР'])
        EquipmentType.objects.create(company=cls.foreign,name='Чужая техника',excel_aliases=['ЭО-4225'])

    def setUp(self):self.client.force_login(self.user)

    def test_job_associations_are_case_insensitive_partial_and_keep_staffing_filters(self):
        url=reverse('resources:brigade_list')
        for value in ['руководитель смр','ПРОРАБ','монтажного   участка','Мастер']:
            page=self.client.get(url,{'name':value})
            self.assertEqual(list(page.context['brigades']),[self.job])
        page=self.client.get(url,{'name':'Руководитель СМР','staffing_current':''})
        self.assertEqual(set(page.context['brigades'].values_list('pk',flat=True)),{self.job.pk,self.old.pk})
        page=self.client.get(url,{'name':'Руководитель СМР','group':self.group.pk})
        self.assertEqual(list(page.context['brigades']),[self.job])

    def test_equipment_root_and_model_associations_match_all_views_and_keep_other_filters(self):
        url=reverse('resources:equipment_type_list')
        for view in ['groups','planning','table']:
            page=self.client.get(url,{'name':'эо-422','view':view,'category':self.category.pk})
            self.assertEqual(list(page.context['equipment_types']),[self.root])
            self.assertNotContains(page,'Чужая техника')
        page=self.client.get(url,{'name':'катерпиллер','parent':self.root.pk,'view':'table'})
        self.assertEqual(list(page.context['equipment_types']),[self.model])
        page=self.client.get(url,{'name':'эо-422','is_active':'False'})
        self.assertFalse(page.context['equipment_types'].exists())

    def test_search_matches_each_association_value_and_treats_punctuation_literally(self):
        query=EquipmentType.objects.filter(company=self.company)
        self.assertEqual(list(filter_name(query,'[особая]')),[self.root])
        self.assertFalse(filter_name(query,'["').exists())
        self.assertEqual(list(filter_name(query,'экскаватор  на  гусеницах')),[self.root])
        self.assertEqual(list(filter_name(query,'Экскаватор гусеничный')),[self.root])

    def test_export_matches_alias_filtered_catalogue(self):
        for route,term,expected in [('brigade_export','руководитель смр',self.job.name),('equipment_type_export','ЭО-4225',self.root.name)]:
            response=self.client.get(reverse('resources:'+route),{'name':term})
            self.assertEqual(response.status_code,200)
            book=load_workbook(BytesIO(response.content));rows=list(book.active.values)
            name=rows[0].index('Название');self.assertEqual([row[name] for row in rows[1:]],[expected]);book.close()

    def test_resource_forms_show_and_save_associations_before_submit(self):
        for route,item,form_class,data in [
            ('brigade_update',self.job,BrigadeForm,{'unit':'чел.','group':self.group.pk}),
            ('equipment_type_update',self.root,EquipmentTypeForm,{'unit':'ед.','category':self.category.pk,'crew-TOTAL_FORMS':0,'crew-INITIAL_FORMS':0})]:
            page=self.client.get(reverse('resources:'+route,args=[item.pk]))
            self.assertContains(page,'Ассоциации из Excel')
            self.assertContains(page,'data-excel-associations')
            self.assertLess(page.content.index(b'data-excel-associations'),page.content.index(b'type="submit"'))
            response=self.client.post(reverse('resources:'+route,args=[item.pk]),{
                'name':item.name,'is_active':'on','excel_aliases':'Первое название\nВторое название',**data})
            self.assertEqual(response.status_code,302,response.context['form'].errors if response.status_code==200 else response.content)
            item.refresh_from_db();self.assertEqual(item.excel_aliases,['Первое название','Второе название'])
            form=form_class({'name':item.name,'excel_aliases':'Дубль\nдубль',**data},instance=item)
            self.assertFalse(form.is_valid());self.assertIn('excel_aliases',form.errors)

    def test_resource_selects_search_metadata_preserves_label_and_escapes_aliases(self):
        self.root.excel_aliases=['АТЗ " onmouseover="alert(1)','<script>alert(1)</script>'];self.root.save()
        for model,item in [(Brigade,self.job),(EquipmentType,self.root),(Position,self.job.position_mapping.position)]:
            field=forms.ModelChoiceField(queryset=model.objects.filter(pk=item.pk))
            html=field.widget.render('resource',None)
            self.assertIn('data-search-aliases=',html)
            self.assertIn(str(item),html)
            self.assertNotIn('<script>',html)
            self.assertNotIn(' onmouseover="',html)
        html=forms.ModelChoiceField(queryset=EquipmentType.objects.filter(pk=self.root.pk)).widget.render('resource',None)
        self.assertIn('&lt;script&gt;',html)

    def test_position_aliases_are_prefetched_in_batches_not_per_choice(self):
        positions=list(Position.objects.filter(company=self.company))
        options=[{'value':ModelChoiceIteratorValue(item.pk,item),'attrs':{},'label':item.name} for item in positions]
        with self.assertNumQueries(2):alias_options(options)
        with self.assertNumQueries(0):alias_options(options)
        option=next(o for o in options if o['value'].value==self.job.position_mapping.position_id)
        self.assertIn('Прораб монтажного участка',option['attrs']['data-search-aliases'])
