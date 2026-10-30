import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from uuid import uuid4
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse

@skipUnless(os.environ.get('INSTRUCTIONS_BROWSER_TESTS')=='1','Optional instruction capture and browser verification')
class InstructionsBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_capture_and_read_guides_without_losing_entry(self):
        from apps.production.test_transport import TransportTests
        from apps.personnel.test_lodging import LodgingTests
        from apps.production.test_work_entry_ui import hierarchy_fixture
        from apps.accounts.models import User,Role
        from apps.production.models import DailyFact
        from apps.production.dispatch_models import EmployeeActivity,EmployeeWorkLink
        from apps.production import transport,camp_fuel
        from apps.personnel.models import LodgingStay
        from apps.resources.test_transport_import import TransportImportTests,upload
        from apps.resources import transport_import
        demo=type('TransportDemo',(),{});TransportTests.setUpTestData.__func__(demo)
        home=type('HousingDemo',(),{});LodgingTests.setUpTestData.__func__(home)
        demo.work.name='Монтаж трубопровода';demo.work.save()
        demo.type.name='Экскаватор';demo.type.save();demo.machine.name='Экскаватор учебный';demo.machine.save()
        facility,package,camera,cable,entry,_=hierarchy_fixture(demo)
        demo.package=package
        for work,item,quantity in [(demo.work,None,5),(package,camera,30),(package,cable,0)]:DailyFact.objects.create(company=demo.company,project_work=work,work_item=item,date=demo.day,actual_quantity=quantity,reported_by=demo.users['DISPATCHER'])
        person=EmployeeActivity.objects.create(company=demo.company,construction_object=demo.obj,date=demo.day,employee=demo.employee,position=demo.employee.position,work=demo.work,entry_direction='works',assignment_reviewed=True,reported_by=demo.users['DISPATCHER'])
        EmployeeWorkLink.objects.create(company=demo.company,activity=person,work=demo.work)
        transport.move(demo.admin,demo.camp,demo.day,[demo.machine.pk],'ARRIVAL',condition='READY',signed=transport.snapshot(demo.admin,demo.camp,demo.day))
        transport.save_day(demo.admin,demo.camp,demo.day,[demo.machine.pk],'WORKED',signed=transport.snapshot(demo.admin,demo.camp,demo.day))
        LodgingStay.objects.create(company=home.company,camp=home.a,bed=home.bed,employee=home.second_employee,arrived_on=home.day,recorded_by=home.commandant,keys_issued=True,key_number='12')
        fuel=User.objects.create_user(username='demo-fuel',company=demo.company,role=Role.objects.get(code='FUEL_WAREHOUSE'));fuel.assigned_objects.add(demo.obj)
        camp_fuel.save_day(demo.admin,demo.camp,demo.day,'TOTAL',opening='10.500',received='5.250',consumed='3.150',signed=camp_fuel.token(demo.admin,demo.camp,demo.day,'TOTAL'))
        imported=transport_import.preview(demo.admin,[upload([TransportImportTests.row(demo,year='190')],'Учебная сводка.xlsm')],demo.day);transport_import.apply(demo.admin,imported)
        def actor(user):
            user.last_name='Учебный';user.first_name='Пользователь';user.save(update_fields=['last_name','first_name'])
            client=Client();client.force_login(user);return client.cookies['sessionid'].value
        from urllib.parse import urlencode
        config={'base':self.live_server_url,'chromium':shutil.which('chromium'),'images':str(Path(__file__).resolve().parents[1]/'static/instructions'),
            'production':{'cookie':actor(demo.users['DISPATCHER']),'url':reverse('production:fact_day_workspace')+'?'+urlencode({'construction_object':demo.obj.pk,'date':str(demo.day),'section':'works'})},
            'rotation':{'cookie':actor(home.hr),'url':reverse('personnel:housing')+'?'+urlencode({'camp':home.a.pk,'date':str(home.day)})},
            'lodging':{'cookie':actor(home.commandant),'url':reverse('personnel:lodging')+'?'+urlencode({'camp':home.a.pk,'date':str(home.day)})},
            'fuel':{'cookie':actor(fuel),'url':reverse('production:camp_fuel')+'?'+urlencode({'camp':demo.camp.pk,'date':str(demo.day)})},
            'transport':{'cookie':actor(demo.operator),'url':reverse('production:transport_board')+'?'+urlencode({'camp':demo.camp.pk,'date':str(demo.day)}),'machine':demo.machine.pk},
            'import_url':reverse('resources:transport_import_errors')}
        script=Path(__file__).resolve().parents[1]/'tests/browser/instructions.js'
        result=subprocess.run(['node',str(script)],capture_output=True,text=True,env=dict(os.environ,INSTRUCTIONS_CONFIG=json.dumps(config)),timeout=210)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr);print(result.stdout.strip())
