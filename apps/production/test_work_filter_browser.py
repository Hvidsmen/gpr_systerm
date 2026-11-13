import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from apps.works.models import ComplexObject,ProjectWork,ProjectWorkItem
from . import test_dispatch as fixtures


@skipUnless(os.environ.get('WORK_FILTER_BROWSER_TESTS')=='1','Optional work filter browser scenario')
class WorkFilterBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True

    def test_new_row_filters_packages_and_keeps_legacy(self):
        fixtures.DispatcherTests.setUpTestData.__func__(type(self))
        objects=[]
        for name in ['УЗА 32','УЗА 33']:
            complex_object=ComplexObject.objects.create(company=self.company,section=self.work.section,construction_object=self.obj,name=name)
            package=ProjectWork.objects.create(company=self.company,section=self.work.section,complex_object=complex_object,name='Электроснабжение',kind='COMPOSITE',unit='компл',is_legacy_structure=name=='УЗА 33')
            item=ProjectWorkItem.objects.create(company=self.company,project_work=package,name='Монтаж',unit='шт',quantity_per_unit=1)
            from .models import DailyFact
            DailyFact.objects.create(company=self.company,project_work=package,work_item=item,date=self.day,actual_quantity=1,reported_by=self.users['DISPATCHER'],outside_smg=True)
            objects.append((complex_object,package,item))
        save_object=ComplexObject.objects.create(company=self.company,section=self.work.section,construction_object=self.obj,name='УЗА 34')
        save_work=ProjectWork.objects.create(company=self.company,section=self.work.section,complex_object=save_object,name='Устройство периметрального ограждения',kind='COMPOSITE',unit='компл',is_legacy_structure=True)
        save_item=ProjectWorkItem.objects.create(company=self.company,project_work=save_work,name='Устройство периметрального ограждения',unit='м.п',quantity_per_unit=1)
        self.client.force_login(self.users['DISPATCHER'])
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'cookie':self.client.cookies['sessionid'].value,
            'path':f'{self.url}?construction_object={self.obj.pk}&date={self.day}&section=works','complex':objects[1][0].pk,'work':objects[1][1].pk,'item':objects[1][2].pk,
            'saveComplex':save_object.pk,'saveWork':save_work.pk,'saveItem':save_item.pk,
            'otherWork':objects[0][1].pk}
        result=subprocess.run(['node',str(Path(__file__).resolve().parents[2]/'tests/browser/work_filter.js')],capture_output=True,text=True,env={**os.environ,'WORK_FILTER_CONFIG':json.dumps(config)},timeout=120)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
