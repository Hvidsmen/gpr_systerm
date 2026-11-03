from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor


class ProductionAccessMigrationTests(TransactionTestCase):
    def test_rename_keeps_group_and_user_selections_and_main_task_objects(self):
        previous=[('accounts','0027_accessgroup_production_view_objects_and_more')]
        current=[('accounts','0028_production_additional_objects')]
        try:
            executor=MigrationExecutor(connection);executor.migrate(previous)
            apps=executor.loader.project_state(previous).apps
            company=apps.get_model('accounts','Company').objects.create(name='Migration company')
            role,_=apps.get_model('accounts','Role').objects.get_or_create(code='DISPATCHER',defaults={'name':'Dispatcher'})
            project=apps.get_model('projects','Project').objects.create(company=company,name='Migration project')
            model=apps.get_model('projects','ConstructionObject')
            one=model.objects.create(company=company,project=project,name='KTP-1',code='KTP-1');two=model.objects.create(company=company,project=project,name='KTP-2',code='KTP-2')
            group=apps.get_model('accounts','AccessGroup').objects.create(company=company,role=role,name='Migration group')
            user=apps.get_model('accounts','User').objects.create(username='migration-user',company=company,role=role,access_group=group)
            for record in [user,group]:record.assigned_objects.add(one);record.production_view_objects.add(two)
            executor=MigrationExecutor(connection);executor.migrate(current)
            apps=executor.loader.project_state(current).apps
            for name,identity in [('User',user.pk),('AccessGroup',group.pk)]:
                record=apps.get_model('accounts',name).objects.get(pk=identity)
                self.assertEqual(list(record.assigned_objects.values_list('pk',flat=True)),[one.pk])
                self.assertEqual(list(record.production_additional_objects.values_list('pk',flat=True)),[two.pk])
        finally:
            MigrationExecutor(connection).migrate(current)
