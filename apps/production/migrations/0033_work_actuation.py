from django.db import migrations, models


def transfer_object_actuations(apps, schema_editor):
    """Keep historical day records; expand their scope only onto existing work facts."""
    Day = apps.get_model('production', 'DayActuation')
    Fact = apps.get_model('production', 'DailyFact')
    alias = schema_editor.connection.alias
    for record in Day.objects.using(alias).all().iterator():
        Fact.objects.using(alias).filter(company_id=record.company_id,
            project_work__section__construction_object_id=record.construction_object_id,
            date=record.date, actuation_type='').update(
                actuation_type=record.actuation_type, actuation_reason=record.reason)


class Migration(migrations.Migration):
    dependencies = [('production', '0032_camp_fuel_density')]
    operations = [
        migrations.AddField(model_name='dailyfact', name='actuation_type', field=models.CharField(
            blank=True, default='', max_length=16, choices=[('DAY','Дневная'),('NIGHT','Ночная'),('FULL_DAY','Суточная')], verbose_name='Актировка работы')),
        migrations.AddField(model_name='dailyfact', name='actuation_reason', field=models.TextField(
            blank=True, default='', verbose_name='Причина актировки работы')),
        migrations.RunPython(transfer_object_actuations, migrations.RunPython.noop),
    ]
