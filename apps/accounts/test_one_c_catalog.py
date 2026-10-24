import json
import zlib
from django.test import SimpleTestCase,TestCase
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from .one_c_catalog import catalogue,parse_mapping,table_type
from .one_c_metadata import dbnames,headers,resolve,parse,unpack
from .models import Company,Role,User,OneCConnection,OneCDiagnosticRun

OWNER='11111111-1111-1111-1111-111111111111'
FIELD='22222222-2222-2222-2222-222222222222'
PART='33333333-3333-3333-3333-333333333333'

def serialize(value):
    if isinstance(value,list):return '{'+','.join(serialize(x) for x in value)+'}'
    return '"'+str(value).replace('"','""')+'"'
def binary(value):
    compress=zlib.compressobj(wbits=-15)
    return compress.compress(serialize(value).encode())+compress.flush()
def header(identity,name):return ['0',['0','0',identity],name,['1','ru',name]]
def schema(table='_Reference394',column='_Description'):
    return {'schema':'dbo','table':table,'column':column,'type':'nvarchar'}

class MetadataReaderTests(SimpleTestCase):
    def test_names_link_uuid_to_sql_without_content_guessing(self):
        index=dbnames(binary(['0',['3',[OWNER,'Reference','394'],[FIELD,'Fld','100'],[PART,'VT','5']]]))
        data=binary(['0',['other',header(OWNER,'Должности'),[header(FIELD,'КлассВодителя'),header(PART,'Категории')]]])
        names=headers(data,index)
        result=resolve(index,{OWNER:names},[schema(),schema(column='_Fld100RRef'),schema(table='_Reference394_VT5'),schema(table='_Reference395')])
        self.assertEqual(result['dbo._Reference394']['name'],'Справочник.Должности')
        self.assertEqual(result['dbo._Reference394']['columns']['_Fld100RRef'],'КлассВодителя')
        self.assertEqual(result['dbo._Reference394_VT5']['name'],'Справочник.Должности.Категории')
        self.assertNotIn('dbo._Reference395',result)

    def test_dbnames_legitimate_auxiliary_same_uuid_and_invalid_format(self):
        index=dbnames(binary(['0',['2',[OWNER,'ReferenceChngR','390'],[OWNER,'Reference','394']]]))
        self.assertEqual(index[OWNER]['prefix'],'Reference')
        self.assertEqual(index[OWNER]['aliases'][0]['prefix'],'ReferenceChngR')
        with self.assertRaises(ValueError):dbnames(binary(['not DBNames']))
        with self.assertRaises(ValueError):unpack(b'invalid compressed metadata')
        self.assertEqual(parse('{,"x""y",{}}'),['','x"y',[]])

    def test_types_groups_names_filters_and_unknown_fields(self):
        report={'schema':[schema(),schema(column='_Fld100'),schema(table='_Document99'),schema(table='_InfoRg25'),schema(table='Params')]}
        labels={'dbo._Reference394':{'name':'Справочник.Должности','columns':{'_Fld100':'Класс'}}}
        rows,counts,total=catalogue(report,labels)
        self.assertEqual(total,4)
        self.assertEqual(rows[0]['name'],'Справочник.Должности')
        self.assertEqual(rows[0]['columns'][0]['name'],'Наименование')
        self.assertEqual(len(catalogue(report,labels,'дОлЖн','reference')[0]),1)
        self.assertEqual(len(catalogue(report,labels,'_document99','document')[0]),1)
        self.assertFalse(catalogue(report,labels,'должн','document')[0])
        self.assertEqual(table_type('_Reference394_VT5'),'reference')
        self.assertEqual(table_type('_BPr44'),'business_process')
        self.assertEqual(table_type('_AccumRgT123'),'accumulation')

    def test_import_rejects_foreign_or_duplicate_tables_and_columns(self):
        report={'schema':[schema()]}
        row={'schema':'dbo','table':'_Reference394','name':'Справочник.Должности','columns':{'_Description':'Наименование'}}
        self.assertIn('dbo._Reference394',parse_mapping({'tables':[row]},report))
        for data in [{'tables':[row,row]},{'tables':[{**row,'table':'_Reference999'}]},{'tables':[{**row,'columns':{'unknown':'Поле'}}]}]:
            with self.assertRaises(ValidationError):parse_mapping(data,report)

class MetadataCatalogueViews(TestCase):
    def setUp(self):
        self.company=Company.objects.create(name='Metadata catalogue')
        self.admin=User.objects.create_user(username='metadata-admin',company=self.company,role=Role.objects.get(code='ADMIN'))
        self.connection=OneCConnection.objects.create(company=self.company,server='msk-db',database='zupcorp01_ivanov')
        OneCDiagnosticRun.objects.create(connection=self.connection,server=self.connection.server,database=self.connection.database,status='DONE',report={'schema':[schema(),schema(table='_Document10')],'metadata_labels':{'dbo._Reference394':{'name':'Справочник.Должности','columns':{},'source':'Params.DBNames + Config'}}})
        self.client.force_login(self.admin);self.url=reverse('accounts:one_c')

    def test_server_side_search_type_and_grouping(self):
        response=self.client.get(self.url,{'q':'Должности','type':'reference'})
        self.assertContains(response,'Справочник.Должности')
        self.assertEqual(len(response.context['catalogue_page']),1)
        self.assertEqual(response.context['catalogue_groups'][0]['label'],'Справочники')
        self.assertFalse(self.client.get(self.url,{'q':'Должности','type':'document'}).context['catalogue_page'].object_list)

    def test_candidate_structure_remains_available_when_catalogue_is_filtered(self):
        run=self.connection.runs.get()
        run.report['candidates']=[{'schema':'dbo','table':'_Reference394','matched_rows':1,'sampled_rows':2}]
        run.report['schema'][0].update(max_length=200,is_nullable=False)
        run.save(update_fields=['report'])
        response=self.client.get(self.url,{'type':'document'})
        candidate=response.context['latest'].report['candidates'][0]
        self.assertEqual(candidate['columns'][0]['column'],'_Description')
        self.assertEqual(candidate['columns'][0]['name'],'Наименование')
        self.assertContains(response,'Структура · 1 полей')
        self.assertContains(response,'Макс. длина, байт')
        self.assertContains(response,'nvarchar')

    def test_mapping_persists_and_is_not_used_for_different_connection(self):
        upload=SimpleUploadedFile('mapping.json',json.dumps({'tables':[{'schema':'dbo','table':'_Reference394','name':'Справочник.Должности','columns':{}}]}).encode())
        self.assertEqual(self.client.post(self.url,{'action':'mapping','mapping':upload}).status_code,302)
        self.connection.refresh_from_db();self.assertEqual(self.connection.metadata_database,'zupcorp01_ivanov')
        self.connection.database='another_database';self.connection.save()
        response=self.client.get(self.url)
        self.assertEqual(response.context['mapped_count'],0)
        self.assertIsNone(response.context['latest'])
        self.assertEqual(response.context['catalogue_total'],0)

class MetadataCommandTests(SimpleTestCase):
    def test_private_snapshot_index_and_decode_commands(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        parser=Path(__file__).resolve().parents[2]/'ops/parse_1c_metadata.py'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'dbnames.bin').write_bytes(binary(['0',['2',[OWNER,'Reference','394'],[FIELD,'Fld','100']]]))
            result=subprocess.run([sys.executable,str(parser),'index',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((root/'requests.json').read_text())['objects'][0]['uuid'],OWNER)
            (root/(OWNER+'.bin')).write_bytes(binary(['0',[header(OWNER,'Должности'),header(FIELD,'КлассВодителя')]]))
            (root/'schema.json').write_text(json.dumps([schema(),schema(column='_Fld100')]),encoding='utf-8-sig')
            result=subprocess.run([sys.executable,str(parser),'decode',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            data=json.loads((root/'names.json').read_text())
            self.assertEqual(data['labels']['dbo._Reference394']['name'],'Справочник.Должности')

    def test_command_failure_is_structured_without_stderr_traceback(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        parser=Path(__file__).resolve().parents[2]/'ops/parse_1c_metadata.py'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'dbnames.bin').write_bytes(b'not-deflate')
            result=subprocess.run([sys.executable,str(parser),'index',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,1)
            self.assertNotIn('Traceback',result.stderr)
            error=json.loads((root/'reader-error.json').read_text())
            self.assertEqual(error['stage'],'index')
            self.assertEqual(error['error_type'],'ValueError')
            self.assertIn('Unsupported compressed',error['error'])
            self.assertEqual(error['dbnames_bytes'],11)

class MetadataMultipleBindingsTests(SimpleTestCase):
    def test_empty_service_uuid_does_not_abort_index(self):
        empty='00000000-0000-0000-0000-000000000000'
        index=dbnames(binary(['0',['3',[empty,'Reference','1'],[empty,'Document','2'],[OWNER,'Reference','394']]]))
        self.assertNotIn(empty,index)
        self.assertEqual(list(index),[OWNER])

    def test_only_existing_main_binding_is_chosen_regardless_of_input_order(self):
        entries=[[OWNER,'Reference','12'],[OWNER,'Reference','394'],[FIELD,'Fld','99'],[FIELD,'Fld','100']]
        for values in [entries,list(reversed(entries))]:
            index=dbnames(binary(['0',['4',*values]]))
            self.assertEqual(len(index[OWNER]['variants']),2)
            names={OWNER:'Должности',FIELD:'Класс'}
            result=resolve(index,{OWNER:names},[schema(),schema(column='_Fld100')])
            self.assertEqual(result['dbo._Reference394']['name'],'Справочник.Должности')
            self.assertEqual(result['dbo._Reference394']['columns']['_Fld100'],'Класс')

    def test_ambiguous_object_does_not_prevent_other_objects_from_loading(self):
        other='44444444-4444-4444-4444-444444444444'
        index=dbnames(binary(['0',['3',[OWNER,'Reference','12'],[OWNER,'Reference','394'],[other,'Document','9']]]))
        warnings=[]
        result=resolve(index,{OWNER:{OWNER:'Должности'},other:{other:'ПриёмНаРаботу'}},[schema(),schema(table='_Reference12'),schema(table='_Document9')],warnings)
        self.assertEqual(set(result),{'dbo._Document9'})
        self.assertIn('Several main SQL tables',warnings[0]['error'])

class MetadataManifestTests(SimpleTestCase):
    def test_multiple_metadata_objects_have_named_array_for_powershell(self):
        import subprocess,sys,tempfile
        from pathlib import Path
        second='44444444-4444-4444-4444-444444444444'
        parser=Path(__file__).resolve().parents[2]/'ops/parse_1c_metadata.py'
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'dbnames.bin').write_bytes(binary(['0',['2',[OWNER,'Reference','394'],[second,'Document','5']]]))
            result=subprocess.run([sys.executable,str(parser),'index',str(root)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            manifest=json.loads((root/'requests.json').read_text())
            self.assertIsInstance(manifest,dict)
            self.assertEqual({row['uuid'] for row in manifest['objects']},{OWNER,second})
            self.assertTrue(all(isinstance(row['uuid'],str) for row in manifest['objects']))
