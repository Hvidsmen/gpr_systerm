import json,os,shutil,subprocess
from pathlib import Path
from unittest import skipUnless
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client
from django.urls import reverse
from apps.accounts.models import User,Role
from apps.resources.models import Equipment,EquipmentType,CatalogueReviewRequest
from . import transport,test_transport_planning as fixtures


@skipUnless(os.environ.get('TRANSPORT_POOL_BROWSER_TESTS')=='1','Optional whole-fleet transport flow')
class TransportPoolBrowserTests(StaticLiveServerTestCase):
    serialized_rollback=True
    def test_whole_fleet_grouped_selection_movement_subset_and_both_reviews(self):
        fixtures.setup_data(type(self))
        transport.move(self.operator,self.camp,self.day,[self.machine.pk,self.bus.pk],'ARRIVAL',signed=transport.snapshot(self.operator,self.camp,self.day))
        remote=Equipment.objects.create(company=self.company,type=self.type,name='Remote crane',inventory_number='REMOTE-1')
        transport.move(self.operator,self.target,self.day,[remote.pk],'ARRIVAL',signed=transport.snapshot(self.operator,self.target,self.day))
        model=EquipmentType.objects.create(company=self.company,parent=self.type,name='Catalog model',brand_model='CAT 320')
        reviewer=User.objects.create_user(username='browser-equipment-reviewer',company=self.company,role=Role.objects.get(code='EQUIPMENT_CATALOG_MANAGER'))
        self.client.force_login(self.operator);other=Client();other.force_login(reviewer)
        config=dict(base=self.live_server_url,chromium=shutil.which('chromium'),cookie=self.client.cookies['sessionid'].value,reviewer_cookie=other.cookies['sessionid'].value,url=self.board,camp=self.camp.pk,day=str(self.day),type=self.type.pk,empty=self.planned_only.pk,machine=self.machine.pk,second=self.second.pk,remote=remote.pk,bus=self.bus.pk,model=model.pk,group=self.category.pk,review_base=reverse('resources:catalogue_review_detail',args=[999999]).replace('999999','REVIEWID'))
        script=Path(__file__).resolve().parents[2]/'tests/browser/transport_pool.js'
        result=subprocess.run(['node',str(script)],text=True,capture_output=True,env=dict(os.environ,TRANSPORT_POOL_CONFIG=json.dumps(config)),timeout=150)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertEqual(remote.camp_stays.get().camp,self.target)
        self.assertEqual(self.second.transport_days.get(date=self.day).planning_type_id,self.planned_only.pk)
        machine=Equipment.objects.get(inventory_number='BROWSER-UNIT-NEW')
        self.assertEqual(machine.name,'Browser new planning type');self.assertFalse(machine.camp_stays.exists())
        self.assertEqual(CatalogueReviewRequest.objects.get(machine=machine).status,'CONFIRMED')
        self.assertEqual(CatalogueReviewRequest.objects.get(equipment_type=machine.type).status,'PENDING')
        print(result.stdout.strip())
