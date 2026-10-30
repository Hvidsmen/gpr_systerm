from datetime import timedelta
from uuid import uuid4
from django.test import TestCase
from django.urls import reverse
from django.core.exceptions import ValidationError
from apps.accounts.models import Company,Role,User
from apps.resources.models import Equipment,EquipmentType,CatalogueReviewRequest
from apps.planning.models import WorkTask
from apps.resources import catalogue_review
from . import transport,test_transport_planning as fixtures
from .transport_models import EquipmentDayRecord,EquipmentCampStay
from .equipment_pool import prepare


class EquipmentPoolTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures.setup_data(cls)
    def setUp(self):self.client.force_login(self.operator)
    def assign(self,machine,kind=None,camp=None,**values):
        camp=camp or self.camp
        payload=dict(camp=camp.pk,date=str(self.day),equipment=machine.pk,field='assign',value=(kind or self.type).pk,signed=transport.snapshot(self.operator,camp,self.day),session=str(uuid4()))
        payload.update(values)
        return self.client.post(reverse('production:transport_cell'),payload,content_type='application/json')
    def page(self,tab='day'):
        return self.client.get(self.board,dict(camp=self.camp.pk,date=str(self.day),tab=tab))
    def test_full_pool_locations_assignment_without_movement_and_saved_edit(self):
        transport.move(self.operator,self.target,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(self.operator,self.target,self.day))
        pool=prepare(self.company,self.camp,self.day)
        self.assertEqual({r['id'] for r in pool},{self.machine.pk,self.second.pk,self.bus.pk})
        self.assertEqual(next(r for r in pool if r['id']==self.machine.pk)['location'],1)
        self.assertEqual(next(r for r in pool if r['id']==self.second.pk)['location'],2)
        session=str(uuid4());transport.claim(self.operator,self.camp,self.day,[self.machine.pk],'acquire',session,purpose='day')
        response=self.assign(self.machine,session=session);self.assertEqual(response.status_code,200,response.content)
        record=self.machine.transport_days.get(date=self.day);self.assertFalse(record.presence_required)
        self.assertEqual(self.machine.camp_stays.count(),1);self.assertEqual(self.machine.camp_stays.get().camp,self.target)
        self.assertEqual(self.assign(self.second,self.planned_only).status_code,200)
        self.assertFalse(self.second.camp_stays.exists())
        self.assertEqual(self.client.get(reverse('production:transport_history',args=[self.second.pk])).status_code,200)
        page=self.page();self.assertEqual(len(page.context['rows']),2);self.assertContains(page,'Нахождение в ВЖГ не оформлено')
        row=next(r for r in page.context['planning_types'] if r['id']==self.planned_only.pk)
        self.assertEqual((row['worked'],row['available']),(1,0))
        payload=dict(camp=self.camp.pk,date=str(self.day),equipment=self.machine.pk,field='status',value='IDLE',signed=transport.snapshot(self.operator,self.camp,self.day),session=str(uuid4()))
        self.assertEqual(self.client.post(reverse('production:transport_cell'),payload,content_type='application/json').status_code,200)
        self.assertEqual(self.assign(self.machine,camp=self.target).status_code,409)
        self.assertNotIn(self.machine.pk,{r['id'] for r in prepare(self.company,self.camp,self.day)})
    def test_disabled_written_off_foreign_company_inaccessible_camp_and_stale_are_rejected(self):
        self.second.is_active=False;self.second.save()
        self.assertNotIn(self.second.pk,{r['id'] for r in prepare(self.company,self.camp,self.day)})
        self.assertEqual(self.assign(self.second).status_code,409)
        self.assertEqual(self.assign(self.machine,camp=self.unrelated).status_code,200)
        self.assertEqual(self.assign(self.machine,value=999999).status_code,409)
        other=Company.objects.create(name='Другие машины');foreign=Equipment(company=other,type=self.type,name='Чужая',plate_number='foreign');Equipment.objects.bulk_create([foreign])
        self.assertEqual(self.assign(foreign).status_code,404)
    def test_day_cleanup_state_row_actions_and_fuel_navigation(self):
        transport.move(self.operator,self.camp,self.day,[self.machine.pk],'ARRIVAL',signed=transport.snapshot(self.operator,self.camp,self.day))
        page=self.page()
        for forbidden in ['data-transport-action="ARRIVAL"','data-transport-action="TRANSFER"','data-transport-action="DEPARTURE"','data-transport-action="WRITEOFF"','data-new-machine>','data-transport-view=','transport-summary','ГСМ · приход']:
            self.assertNotContains(page,forbidden)
        state=self.page('state');self.assertNotContains(state,'data-transport-action="ARRIVAL"')
        for action in ['TRANSFER','DEPARTURE','WRITEOFF']:self.assertContains(state,'data-machine-action="'+action+'"')
        for tab in ['state','events','report','calendar']:self.assertNotContains(self.page(tab),'ГСМ · приход')
        fuel=self.client.get(reverse('production:camp_fuel'),dict(camp=self.camp.pk,date=str(self.day)))
        self.assertNotContains(fuel,'>Техника</a>');self.assertNotContains(fuel,'ГСМ по объекту')


class TransportCatalogueTests(EquipmentPoolTests):
    # Separate only the catalogue cases from pool assertions when discovering tests.
    def setUp(self):
        super().setUp()
        self.reviewer=User.objects.create_user(username='transport-reviewer',company=self.company,role=Role.objects.get(code='EQUIPMENT_CATALOG_MANAGER'))
    def propose(self,name='Новая техника'):
        return self.client.post(reverse('production:transport_type_propose'),dict(camp=self.camp.pk,name=name,group=self.category.pk),content_type='application/json')
    def create_machine(self,kind,**extra):
        values={'new-machine-type':kind.pk,'new-machine-inventory_number':'UNIT-NEW','new-machine-condition':'READY',**extra}
        return self.client.post(reverse('production:transport_create')+'?camp='+str(self.camp.pk)+'&date='+str(self.day),values,HTTP_ACCEPT='application/json')
    def test_planning_proposal_creates_responsible_task_and_machine_has_separate_review(self):
        response=self.propose();self.assertEqual(response.status_code,200,response.content)
        kind=EquipmentType.objects.get(pk=response.json()['id']);self.assertFalse(kind.is_active)
        proposal=CatalogueReviewRequest.objects.get(equipment_type=kind)
        self.assertEqual(WorkTask.objects.get(catalogue_request=proposal).assigned_to,self.reviewer)
        self.assertEqual(self.propose('  НОВАЯ   ТЕХНИКА ').status_code,400)
        response=self.create_machine(kind);self.assertEqual(response.status_code,200,response.content)
        machine=Equipment.objects.get(pk=response.json()['id']);self.assertEqual(machine.name,kind.name)
        unit_review=CatalogueReviewRequest.objects.get(machine=machine)
        self.assertEqual(WorkTask.objects.get(catalogue_request=unit_review).assigned_to,self.reviewer)
        self.client.force_login(self.reviewer)
        page=self.client.get(reverse('resources:catalogue_review_detail',args=[unit_review.pk]));self.assertEqual(page.status_code,200)
        self.assertContains(page,'UNIT-NEW');self.assertContains(page,'data-machine-review')
        self.assertNotIn('replace',dict(page.context['form'].fields['action'].choices))
        catalogue_review.decide(self.reviewer,unit_review,'confirm',0)
        catalogue_review.decide(self.reviewer,proposal,'confirm',0)
        self.assertEqual(WorkTask.objects.get(catalogue_request=unit_review).status,'DONE')
    def test_model_sets_brand_and_generated_name_server_side_and_name_is_absent(self):
        model=EquipmentType.objects.create(company=self.company,parent=self.type,name='Модель',brand_model='Модель из каталога')
        response=self.create_machine(self.type,**{'new-machine-model':model.pk,'new-machine-brand_model':'Подмена','new-machine-name':'Лишнее'})
        self.assertEqual(response.status_code,200,response.content)
        machine=Equipment.objects.get(pk=response.json()['id']);self.assertEqual(machine.brand_model,'Модель из каталога');self.assertEqual(machine.name,'Модель из каталога')
        self.assertNotContains(self.page('state'),'name="new-machine-name"')
    def test_replacement_reclassifies_machine_and_rejected_unit_is_disabled(self):
        kind=EquipmentType.objects.get(pk=self.propose().json()['id']);self.create_machine(kind)
        proposal=CatalogueReviewRequest.objects.get(equipment_type=kind)
        token,_=catalogue_review.preview(self.reviewer,proposal,self.type)
        catalogue_review.decide(self.reviewer,proposal,'replace',0,target=self.type,token=token)
        machine=Equipment.objects.get(inventory_number='UNIT-NEW');self.assertEqual(machine.type,self.type)
        unit_review=CatalogueReviewRequest.objects.get(machine=machine)
        catalogue_review.decide(self.reviewer,unit_review,'reject',0,comment='Проверьте номер')
        machine.refresh_from_db();self.assertFalse(machine.is_active)
        self.assertEqual(WorkTask.objects.get(catalogue_request=unit_review).status,'DONE')
