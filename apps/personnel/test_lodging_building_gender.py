from io import BytesIO
from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook
from apps.production.history_models import FactRevision
from . import test_lodging as fixtures
from .models import LodgingBuilding,LodgingBed,LodgingStay
from .lodging_forms import LodgingMovementForm,BuildingForm


class LodgingBuildingGenderTests(TestCase):
    setUpTestData=classmethod(fixtures.LodgingTests.setUpTestData.__func__)
    def setUp(self):
        self.client.force_login(self.commandant)
        self.scope={'camp':self.a.pk,'date':str(self.day)}
    def building_payload(self,**extra):
        self.building.refresh_from_db()
        data=dict(self.scope,name=self.building.name,inventory_number=self.building.inventory_number,description=self.building.description,kind=self.building.kind,is_active='on',version=self.building.updated_at.isoformat())
        data.update(extra);return data

    def test_new_building_gender_and_beds_are_saved(self):
        url=reverse('personnel:lodging_building_create')
        for gender in ['M','F','MIXED','']:
            response=self.client.post(url,dict(self.scope,name='Вагон '+(gender or 'без уточнения'),gender=gender,places='2',is_active='on'))
            self.assertEqual(response.status_code,302)
            building=LodgingBuilding.objects.get(name='Вагон '+(gender or 'без уточнения'))
            self.assertEqual(building.gender,gender);self.assertEqual(building.beds.count(),2)

    def test_edit_is_visible_in_header_choices_and_history(self):
        url=reverse('personnel:lodging_building_edit',args=[self.building.pk])
        self.assertEqual(self.client.post(url,self.building_payload(gender='F',description='Жилой вагон на 8 мест')).status_code,302)
        page=self.client.get(reverse('personnel:lodging'),self.scope)
        self.assertContains(page,'data-building-gender="F"');self.assertContains(page,'Пол: Женский')
        form=LodgingMovementForm(company=self.company,camp=self.a,day=self.day)
        self.assertIn('Пол вагона: Женский',form.fields['bed'].label_from_instance(form.fields['bed'].queryset.get(pk=self.bed.pk)))
        choice=next(b for b in self.client.get(reverse('personnel:lodging_available_beds'),self.scope).json()['beds'] if b['id']==self.bed.pk)
        self.assertEqual(choice['building_gender'],'F');self.assertIn('Женский',choice['label']);self.assertIn('Вагон 1 · Жилой вагон на 8 мест · место 1',choice['label'])
        revision=FactRevision.objects.filter(entity_type='personnel.lodgingbuilding',entity_id=str(self.building.pk),actor=self.commandant).latest('pk')
        self.assertEqual(revision.before['fields']['gender'],'');self.assertEqual(revision.after['fields']['gender'],'F')

    def test_employee_gender_and_stay_are_independent_of_building_metadata(self):
        self.second_employee.gender='M';self.second_employee.save()
        stay=fixtures.LodgingTests.edit(self)
        before=(stay.arrived_on,stay.departed_on,stay.bed_id)
        url=reverse('personnel:lodging_building_edit',args=[self.building.pk])
        self.assertEqual(self.client.post(url,self.building_payload(gender='F')).status_code,302)
        self.second_employee.refresh_from_db();stay.refresh_from_db()
        self.assertEqual(self.second_employee.gender,'M');self.assertEqual((stay.arrived_on,stay.departed_on,stay.bed_id),before)
        export=self.client.get(reverse('personnel:lodging'),dict(self.scope,export='xlsx'))
        values=list(load_workbook(BytesIO(export.content)).active.values);header=values[0]
        row=next(row for row in values[1:] if row[header.index('ФИО')]==self.second_employee.display_name)
        self.assertEqual(row[header.index('Пол')],'Мужской');self.assertEqual(row[header.index('Пол вагона')],'Женский')
        self.assertEqual(header[-1],'Пол вагона')

    def test_unknown_is_default_and_legacy_edit_does_not_clear_gender(self):
        self.assertEqual(self.building.gender,'')
        self.building.gender='M';self.building.save()
        url=reverse('personnel:lodging_building_edit',args=[self.building.pk])
        self.assertEqual(self.client.post(url,self.building_payload()).status_code,302)
        self.building.refresh_from_db();self.assertEqual(self.building.gender,'M')
        self.assertEqual(self.client.post(url,self.building_payload(gender='')).status_code,302)
        self.building.refresh_from_db();self.assertEqual(self.building.gender,'')

    def test_quick_creation_saves_gender_and_existing_building_is_unchanged(self):
        url=reverse('personnel:lodging_quick_bed')
        response=self.client.post(url,dict(self.scope,new_building_name='12',new_building_description='Жилой вагон на 8 мест',new_building_gender='F',number='9'))
        self.assertEqual(response.status_code,201)
        data=response.json();bed=LodgingBed.objects.get(pk=data['id'])
        self.assertEqual(bed.building.gender,'F');self.assertEqual(bed.building.description,'Жилой вагон на 8 мест');self.assertIn('Вагон 12 · Жилой вагон на 8 мест · место 9',data['label']);self.assertIn('Женский',data['label']);self.assertIn('Женский',data['building']['label'])
        bad=self.client.post(url,dict(self.scope,building=self.building.pk,new_building_gender='M',number='9'))
        self.assertEqual(bad.status_code,400);self.building.refresh_from_db();self.assertEqual(self.building.gender,'')

    def test_invalid_choice_and_stale_version_do_not_change_building(self):
        url=reverse('personnel:lodging_building_edit',args=[self.building.pk])
        self.assertEqual(self.client.post(url,self.building_payload(gender='invalid')).status_code,200)
        self.building.refresh_from_db();self.assertEqual(self.building.gender,'')
        stale=self.building_payload(gender='F')
        self.building.gender='M';self.building.save()
        response=self.client.post(url,stale)
        self.assertContains(response,'Вагон уже изменён')
        self.building.refresh_from_db();self.assertEqual(self.building.gender,'M')

    def test_company_and_role_permissions(self):
        foreign=reverse('personnel:lodging_building_edit',args=[self.foreign_building.pk])
        self.assertEqual(self.client.get(foreign,self.scope).status_code,404)
        url=reverse('personnel:lodging_building_edit',args=[self.building.pk])
        self.client.force_login(self.hr)
        self.assertEqual(self.client.post(url,self.building_payload(gender='F')).status_code,403)
