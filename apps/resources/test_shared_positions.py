from django.test import TestCase
from django.urls import reverse
from apps.planning.test_workspace import WorkspaceTests
from .models import Brigade, Position, PositionPlanningBrigade, EquipmentCrewPosition
from .crew_views import CrewForm
from .position_catalog import position_for, brigade_for


class SharedPositionTests(TestCase):
    setUpTestData = classmethod(WorkspaceTests.setUpTestData.__func__)

    def test_general_catalog_entry_is_available_in_crew_and_rename_is_shared(self):
        brigade = Brigade.objects.create(company=self.company, name='Водитель общего справочника')
        position = position_for(brigade)
        form = CrewForm(equipment=self.equipment)
        self.assertIn(position, form.fields['position'].queryset)
        brigade.name = 'Водитель категории C'; brigade.save()
        form = CrewForm({'position':position.pk, 'count':2, 'is_replacement':'on'},equipment=self.equipment)
        self.assertTrue(form.is_valid(), form.errors)
        row = form.save()
        self.assertEqual(row.position_id, position.pk)
        self.assertEqual(brigade_for(row.position), brigade.pk)
        self.assertEqual(row.position.name, brigade.name)
        self.assertTrue(row.is_replacement)
        self.assertEqual(row.count,2)
        self.assertEqual(PositionPlanningBrigade.objects.filter(brigade=brigade).count(),1)

    def test_inactive_and_foreign_positions_cannot_be_posted(self):
        inactive = Brigade.objects.create(company=self.company, name='Неактивная', is_active=False)
        from apps.accounts.models import Company
        foreign = Brigade.objects.create(company=Company.objects.create(name='Другая компания'), name='Чужая')
        for brigade in (inactive,foreign):
            form = CrewForm({'position':position_for(brigade).pk,'count':1},equipment=self.equipment)
            self.assertFalse(form.is_valid())
            self.assertIn('position',form.errors)

    def test_legacy_position_links_to_existing_general_catalog_without_duplicate(self):
        # Model historical rows, before the shared-catalogue signal was installed.
        brigade=Brigade(company=self.company,code='LEGACY-X',name='Старый машинист')
        Brigade.objects.bulk_create([brigade])
        position=Position.objects.create(company=self.company,name=brigade.name,default_hourly_rate=125)
        crew=EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=position,count=3,is_replacement=True)
        self.assertEqual(brigade_for(position),brigade.pk)
        self.assertEqual(position_for(brigade).pk,position.pk)
        crew.refresh_from_db()
        self.assertEqual(crew.position.default_hourly_rate,125)
        self.assertEqual(crew.count,3)
        self.assertTrue(crew.is_replacement)
        self.assertEqual(Brigade.objects.filter(company=self.company,name=brigade.name).count(),1)

    def test_old_catalog_route_and_crew_link_use_general_catalog(self):
        self.client.force_login(self.planner)
        self.assertRedirects(self.client.get(reverse('resources:position_list')),reverse('resources:brigade_list'))
        response=self.client.get(reverse('resources:equipment_crew',args=[self.equipment.pk]))
        self.assertContains(response,reverse('resources:brigade_list'))

    def test_backfill_preserves_crew_ids_rates_and_is_repeatable(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace
        old=Position.objects.create(company=self.company,name='Историческая должность',default_hourly_rate=210)
        crew=EquipmentCrewPosition.objects.create(company=self.company,equipment_type=self.equipment,position=old,count=4,is_replacement=True)
        existing=Brigade(company=self.company,code='BACKFILL',name=old.name)
        Brigade.objects.bulk_create([existing])
        unify=import_module('apps.resources.migrations.0012_unify_position_catalog').unify
        unify(apps,SimpleNamespace(connection=connection))
        self.assertEqual(brigade_for(old),existing.pk)
        counts=(Position.objects.count(),Brigade.objects.count(),PositionPlanningBrigade.objects.count())
        unify(apps,SimpleNamespace(connection=connection))
        self.assertEqual(counts,(Position.objects.count(),Brigade.objects.count(),PositionPlanningBrigade.objects.count()))
        crew.refresh_from_db()
        self.assertEqual((crew.position_id,crew.count,crew.is_replacement),(old.pk,4,True))
        self.assertEqual(crew.position.default_hourly_rate,210)
