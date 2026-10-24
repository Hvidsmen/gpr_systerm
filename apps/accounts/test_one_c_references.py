from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from .test_one_c_catalog import schema, binary, header, OWNER, FIELD
from . import test_one_c_catalog as fixtures
from .one_c_metadata import dbnames, headers, resolve, reference_info, attach_references
from .one_c_catalog import catalogue
from .models import Role

TARGET='44444444-4444-4444-4444-444444444444'
TYPE='55555555-5555-5555-5555-555555555555'
OTHER='66666666-6666-6666-6666-666666666666'
OTHER_TYPE='77777777-7777-7777-7777-777777777777'
UNKNOWN='88888888-8888-8888-8888-888888888888'

class ReferenceMetadataTests(SimpleTestCase):
    def test_reference_type_uuid_and_compound_choices_are_resolved(self):
        index=dbnames(binary(['0',[[OWNER,'Reference','394'],[FIELD,'Fld','100'],[TARGET,'Reference','20'],[OTHER,'Document','30']]]))
        # The type descriptor is a sibling of the common property descriptor,
        # not of an arbitrary header in the owner file.
        prop=['0',header(FIELD,'Исполнитель'),['Pattern',['#',TYPE],['#',OTHER_TYPE],['#',UNKNOWN]]]
        target=binary(['0',['0','0','0',TYPE],header(TARGET,'Сотрудники')])
        other=binary(['0',['0','0','0',OTHER_TYPE],header(OTHER,'Задание')])
        # Owner cannot declare the same type as the destination in a valid config.
        source=binary(['0',['0','0','0','99999999-9999-9999-9999-999999999999'],[prop,header(OWNER,'Работы')]])
        blobs={OWNER:source,TARGET:target,OTHER:other}
        raw=[schema(column='_Fld100RRef'),schema(table='_Reference20'),schema(table='_Document30')]
        labels=resolve(index,{key:headers(blob,index) for key,blob in blobs.items()},raw)
        attach_references(labels,index,{key:reference_info(blob,index,key) for key,blob in blobs.items()})
        self.assertEqual(labels['dbo._Reference394']['references']['_Fld100RRef'],['dbo._Document30','dbo._Reference20'])
        self.assertEqual(labels['dbo._Reference394']['unresolved_references']['_Fld100RRef'],1)
        rows,_,_=catalogue({'schema':raw},labels)
        field=next(row for row in rows if row['table']=='_Reference394')['columns'][0]
        self.assertEqual(len(field['targets']),2)
        self.assertTrue(field['is_reference'])

    def test_uuid_outside_type_pattern_does_not_become_a_reference(self):
        index=dbnames(binary([[OWNER,'Reference','394'],[FIELD,'Fld','100']]))
        blob=binary([header(OWNER,'Работы'),header(FIELD,'Имя'),['#',TYPE]])
        self.assertEqual(reference_info(blob,index,OWNER)['properties'],{})

    def test_standard_parent_and_table_part_owner_targets(self):
        labels={'dbo._Reference394':{'name':'Работы','self_targets':['dbo._Reference394'],'owner_targets':['dbo._Reference20']},
                'dbo._Reference394_VT5':{'name':'Работы.Состав','self_targets':['dbo._Reference394']}}
        report={'schema':[schema(column='_ParentIDRRef'),schema(column='_OwnerIDRRef'),schema(table='_Reference20'),schema(table='_Reference394_VT5',column='_Reference394_IDRRef')]}
        rows,_,_=catalogue(report,labels)
        by_table={row['table']:row for row in rows}
        fields=by_table['_Reference394']['columns']
        self.assertEqual(fields[0]['targets'][0]['table'],'_Reference394')
        self.assertEqual(fields[1]['targets'][0]['table'],'_Reference20')
        self.assertEqual(by_table['_Reference394_VT5']['columns'][0]['targets'][0]['table'],'_Reference394')

    def test_enumeration_type_uuid_slot(self):
        index=dbnames(binary([[TARGET,'Enum','20']]))
        blob=binary(['0',['0',TYPE],header(TARGET,'Категории')])
        self.assertEqual(reference_info(blob,index,TARGET)['types'],{TYPE})

class StructureNavigationTests(TestCase):
    def setUp(self):
        fixtures.MetadataCatalogueViews.setUp(self)
        run=self.connection.runs.get()
        run.report['schema'] += [schema(column='_Fld100RRef'),schema(column='_Fld101_RRRef'),schema(table='_Reference20')]
        label=run.report['metadata_labels']['dbo._Reference394']
        label['columns']={'_Fld100RRef':'ОднаСсылка','_Fld101_RRRef':'СоставнаяСсылка'}
        label['references']={'_Fld100RRef':['dbo._Reference20'],'_Fld101_RRRef':['dbo._Reference20','dbo._Document10']}
        run.save(update_fields=['report'])
        self.detail=reverse('accounts:one_c_table',args=['dbo','_Reference394'])

    def test_table_page_and_single_or_multiple_destinations(self):
        response=self.client.get(self.detail)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'Справочник.Должности')
        self.assertContains(response,'Выбрать таблицу (2)')
        self.assertContains(response,reverse('accounts:one_c_table',args=['dbo','_Reference20']))
        self.assertContains(response,reverse('accounts:one_c_table',args=['dbo','_Document10']))
        self.assertContains(self.client.get(self.url),self.detail)
        self.assertEqual(self.client.get(reverse('accounts:one_c_table',args=['dbo','_Reference20'])).status_code,200)

    def test_unknown_table_connection_scope_and_permissions(self):
        self.assertEqual(self.client.get(reverse('accounts:one_c_table',args=['dbo','_Reference999'])).status_code,404)
        self.assertEqual(self.client.post(self.detail).status_code,405)
        self.connection.database='different';self.connection.save()
        self.assertEqual(self.client.get(self.detail).status_code,404)
        self.admin.role=Role.objects.get_or_create(code='VIEWER',defaults={'name':'Наблюдатель'})[0];self.admin.save()
        self.assertEqual(self.client.get(self.detail).status_code,403)

    def test_manual_names_do_not_remove_discovered_references(self):
        self.connection.metadata_server=self.connection.server
        self.connection.metadata_database=self.connection.database
        self.connection.metadata_labels={'dbo._Reference394':{'name':'Проверенное имя','columns':{'_Fld100RRef':'Сотрудник'}}}
        self.connection.save()
        response=self.client.get(self.detail)
        self.assertContains(response,'Сотрудник')
        self.assertContains(response,reverse('accounts:one_c_table',args=['dbo','_Reference20']))
