import json, os, shutil, subprocess,base64
from io import BytesIO
from openpyxl import Workbook
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from . import test_catalogue_association_search as fixtures


@skipUnless(os.environ.get('CATALOGUE_ALIAS_BROWSER_TESTS')=='1','Optional Excel association search Chromium scenario')
class CatalogueAliasBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_association_edit_filters_and_searchable_resource_choices(self):
        fixtures.CatalogueAssociationSearchTests.setUpTestData.__func__(type(self))
        self.client.force_login(self.user)
        book=Workbook();book.active.append(['ID','Название','Ассоциация']);book.active.append([self.root.pk,self.root.name,'Импортированное название']);stream=BytesIO();book.save(stream);book.close()
        config={'xlsx':base64.b64encode(stream.getvalue()).decode(),'base':self.live_server_url,'cookie':self.client.cookies['sessionid'].value,
            'chromium':shutil.which('chromium'),'root':self.root.pk,'job':self.job.pk,
            'position':self.job.position_mapping.position_id,
            'equipment':reverse('resources:equipment_type_list'),'jobs':reverse('resources:brigade_list'),
            'equipment_edit':reverse('resources:equipment_type_update',args=[self.root.pk]),
            'equipment_create':reverse('resources:equipment_type_create'),'employee_create':reverse('resources:employee_create')}
        script=Path(__file__).resolve().parents[2]/'tests/browser/catalogue_alias_search.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,
            env=dict(os.environ,CATALOGUE_ALIAS_CONFIG=json.dumps(config)),timeout=100)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.root.refresh_from_db();self.assertIn('Универсальная машина',self.root.excel_aliases)
        print(result.stdout.strip())
