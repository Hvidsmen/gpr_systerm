from datetime import date
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class IndependentSMGMigrationTests(TransactionTestCase):
    def test_preserves_rows_references_and_unifies_object_revisions(self):
        executor=MigrationExecutor(connection)
        latest=executor.loader.graph.leaf_nodes()
        before=[('planning','0033_planningworkspace_archived_at_and_more'), *[node for node in latest if node[0]=='accounts']]
        try:
            executor.migrate(before)
            apps=executor.loader.project_state(before).apps
            Company=apps.get_model('accounts','Company');User=apps.get_model('accounts','User')
            Project=apps.get_model('projects','Project');Object=apps.get_model('projects','ConstructionObject')
            Version=apps.get_model('planning','GlobalPlanVersion');Schedule=apps.get_model('planning','DailySchedule');Entry=apps.get_model('planning','DailyScheduleEntry')
            company=Company.objects.create(name='Migrate SMG')
            actor=User.objects.create(username='migration-smg',company=company)
            project=Project.objects.create(company=company,code='m',name='Migration')
            obj=Object.objects.create(company=company,project=project,code='m',name='Migration')
            day=date(2026,1,1)
            ids=[];entries=[]
            for number in [1,2]:
                version=Version.objects.create(company=company,construction_object=obj,start_date=day,end_date=date(2026,1,31),version_number=number,status='APPROVED',snapshot={'marker':number})
                schedule=Schedule.objects.create(company=company,source=version,month=day,revision=1,status='APPLIED',created_by=actor)
                entry=Entry.objects.create(schedule=schedule,key='labor:x',kind='labor',label='Saved row',rows=[{'date':'2026-01-05','planned_count':'3'}])
                ids.append(schedule.pk);entries.append(entry.pk)
            executor=MigrationExecutor(connection);executor.migrate(latest)
            apps=executor.loader.project_state(latest).apps
            Schedule=apps.get_model('planning','DailySchedule');Entry=apps.get_model('planning','DailyScheduleEntry')
            self.assertEqual(list(Schedule.objects.filter(pk__in=ids).order_by('pk').values_list('revision',flat=True)),[1,2])
            self.assertEqual(Schedule.objects.filter(pk__in=ids,status='APPLIED').count(),1)
            self.assertEqual(Schedule.objects.get(pk=ids[-1]).status,'APPLIED')
            self.assertEqual(set(Schedule.objects.filter(pk__in=ids).values_list('construction_object_id',flat=True)),{obj.pk})
            self.assertEqual(Entry.objects.filter(pk__in=entries).count(),2)
            self.assertEqual(Entry.objects.get(pk=entries[0]).rows,[{'date':'2026-01-05','planned_count':'3'}])
        finally:
            MigrationExecutor(connection).migrate(latest)
