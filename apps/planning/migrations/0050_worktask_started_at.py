from django.db import migrations, models
from django.db.models import F, OuterRef, Q, Subquery


def restore_acceptances(apps, schema_editor):
    database = schema_editor.connection.alias
    Task = apps.get_model('planning', 'WorkTask')
    Event = apps.get_model('planning', 'WorkTaskEvent')
    events = Event.objects.using(database).filter(task_id=OuterRef('pk'), company_id=OuterRef('company_id'))
    accepted = events.filter(action='Принято в работу').order_by('-created_at', '-pk')
    terminal = events.filter(action__in=['Выполнено', 'Заменено / отменено', 'Принудительно закрыто']).order_by('-created_at', '-pk')
    Task.objects.using(database).filter(status='OPEN', started_at__isnull=True).annotate(
        acceptance=Subquery(accepted.values('created_at')[:1]),
        ended=Subquery(terminal.values('created_at')[:1]),
    ).filter(acceptance__isnull=False).filter(Q(ended__isnull=True) | Q(acceptance__gt=F('ended'))).update(started_at=F('acceptance'))


class Migration(migrations.Migration):
    dependencies = [('planning', '0049_sbo_approval')]
    operations = [
        migrations.AddField(model_name='worktask', name='started_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(restore_acceptances, migrations.RunPython.noop),
    ]
