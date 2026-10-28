import json,os,shutil,subprocess,tempfile
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse


@skipUnless(os.environ.get('TRANSPORT_IMPORT_BROWSER_TESTS')=='1','Optional import and camp fuel Chromium scenario')
class TransportImportBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_import_errors_daily_details_fuel_concurrency_and_mobile(self):
        from .test_transport_import import TransportImportTests,upload
        TransportImportTests.setUpTestData.__func__(type(self))
        first=Client();first.force_login(self.admin);second=Client();second.force_login(self.colleague)
        row=TransportImportTests.row(self)
        data=dict(zip(__import__('apps.resources.transport_import',fromlist=['FIELDS']).FIELDS,row))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'Сводка1.xlsm';path.write_bytes(upload([row,TransportImportTests.row(self,inventory='INV-200',plate='A200')]).read())
            path2=Path(temp)/'Сводка2.xlsm';path2.write_bytes(upload([row]).read())
            config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':first.cookies['sessionid'].value,'second_cookie':second.cookies['sessionid'].value,
                'upload':reverse('resources:transport_import'),'errors':reverse('resources:transport_import_errors'),'fuel':reverse('production:camp_fuel'),
                'board':reverse('production:transport_board'),'camp':self.camp.pk,'planning_type':self.type.pk,'day':str(self.day),'first':str(path),'second':str(path2)}
            script=Path(__file__).resolve().parents[2]/'tests/browser/transport_import.js'
            result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,IMPORT_CONFIG=json.dumps(config)),timeout=180)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        print(result.stdout.strip())
