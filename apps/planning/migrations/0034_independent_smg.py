from django.db import migrations, models
import django.db.models.deletion


def populate(apps, schema_editor):
    Schedule = apps.get_model('planning', 'DailySchedule')
    counters = {}; applied = set()
    for row in Schedule.objects.select_related('source').order_by('-updated_at', '-pk').iterator():
        row.construction_object_id = row.source.construction_object_id
        key = (row.construction_object_id, row.month)
        if row.status == 'APPLIED':
            if key in applied: row.status = 'ARCHIVED'
            applied.add(key)
        row.save(update_fields=['construction_object', 'status'])
    for row in Schedule.objects.order_by('pk').iterator():
        key = (row.construction_object_id, row.month)
        counters[key] = counters.get(key, 0) + 1
        row.revision = counters[key]
        row.save(update_fields=['revision'])


class Migration(migrations.Migration):
    dependencies = [('planning', '0033_planningworkspace_archived_at_and_more')]
    operations = [
        migrations.RemoveConstraint('dailyschedule', 'unique_daily_schedule_revision'),
        migrations.RemoveConstraint('dailyschedule', 'unique_applied_daily_schedule'),
        migrations.AddField('dailyschedule', 'construction_object', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='daily_schedules', to='projects.constructionobject')),
        migrations.AlterField('dailyschedule', 'source', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='daily_schedules', to='planning.globalplanversion')),
        migrations.RunPython(populate, migrations.RunPython.noop),
        migrations.AlterField('dailyschedule', 'construction_object', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='daily_schedules', to='projects.constructionobject')),
        migrations.AddConstraint('dailyschedule', models.UniqueConstraint(fields=['construction_object', 'month', 'revision'], name='unique_object_daily_schedule_revision')),
        migrations.AddConstraint('dailyschedule', models.UniqueConstraint(fields=['construction_object', 'month'], condition=models.Q(status='APPLIED'), name='unique_object_applied_daily_schedule')),
    ]
