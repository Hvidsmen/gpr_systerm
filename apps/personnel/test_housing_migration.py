from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from django.test import SimpleTestCase
from django.db import connection,connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor


class HousingMigrationTests(SimpleTestCase):
    databases={'default'}

    def test_legacy_camps_calendars_and_unique_identity_are_preserved_without_guessing_links(self):
        with TemporaryDirectory() as directory:
            alias='housing_migration_probe'
            db=DatabaseWrapper({**connection.settings_dict,'NAME':str(Path(directory)/'housing.sqlite3')},alias=alias)
            connections[alias]=db
            try:
                old=[('personnel','0001_initial'),('resources','0018_brigadegroup_is_general_staff')]
                executor=MigrationExecutor(db);executor.migrate(old);apps=executor.loader.project_state(old).apps
                def create(app,model,**data):return apps.get_model(app,model).objects.using(alias).create(**data)
                company=create('accounts','Company',name='Legacy housing')
                board=create('personnel','Board',company_id=company.pk,name='Original board')
                group=create('personnel','StaffingGroup',company_id=company.pk,board_id=board.pk,position='Монтажник')
                employee=create('resources','Employee',company_id=company.pk,last_name='Иванов',first_name='Иван',employee_number='001')
                person=create('personnel','Person',company_id=company.pk,full_name='Иванов Иван',employee_number='001')
                slot=create('personnel','StaffingSlot',company_id=company.pk,group_id=group.pk,person_id=person.pk,camp=' ВЖГ КТП-1 ',status='МВ',arrival_date=date(2020,1,1),calendar={'2020-01-01':'10'})
                ambiguous=create('personnel','Person',company_id=company.pk,full_name='Неоднозначный Работник')
                for camp in ['вжг ктп-1','Базовый ВЖГ']:
                    create('personnel','StaffingSlot',company_id=company.pk,group_id=group.pk,person_id=ambiguous.pk,camp=camp,status='МВ',arrival_date=date(2020,1,1))
                create('resources','Employee',company_id=company.pk,last_name='Тёзка',first_name='Работник')
                same1=create('personnel','Person',company_id=company.pk,full_name='Тёзка Работник')
                same2=create('personnel','Person',company_id=company.pk,full_name='Тёзка Работник')
                other=create('accounts','Company',name='Other legacy company')
                other_board=create('personnel','Board',company_id=other.pk,name='Other board')
                other_group=create('personnel','StaffingGroup',company_id=other.pk,board_id=other_board.pk,position='Монтажник')
                create('personnel','StaffingSlot',company_id=other.pk,group_id=other_group.pk,camp='ВЖГ КТП-1')
                latest=[('personnel','0003_bootstrap_camps')]
                executor=MigrationExecutor(db);executor.migrate(latest);state=executor.loader.project_state(latest).apps
                def query(model):return state.get_model('personnel',model).objects.using(alias)
                self.assertEqual(query('Camp').filter(company_id=company.pk).count(),2)
                self.assertEqual(query('Camp').filter(company_id=other.pk).count(),1)
                migrated=query('Person').get(pk=person.pk)
                self.assertEqual(migrated.employee_id,employee.pk);self.assertEqual(migrated.schedule_slot_id,slot.pk)
                preserved=query('StaffingSlot').get(pk=slot.pk)
                self.assertEqual(preserved.camp,' ВЖГ КТП-1 ');self.assertEqual(preserved.calendar,{'2020-01-01':'10'})
                self.assertEqual(preserved.status,'МВ');self.assertIsNotNone(preserved.housing_camp_id)
                self.assertEqual(query('CampStay').filter(person_id=person.pk).count(),1)
                self.assertFalse(query('CampStay').filter(person_id=ambiguous.pk).exists())
                self.assertFalse(query('CampObjectLink').exists())
                self.assertIsNone(query('Person').get(pk=same1.pk).employee_id)
                self.assertIsNone(query('Person').get(pk=same2.pk).employee_id)
            finally:
                db.close()
                if hasattr(connections._connections,alias):del connections[alias]
