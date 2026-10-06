from django.db import migrations

DEFAULTS = [('м','Метр'),('м²','Квадратный метр'),('м³','Кубический метр'),('шт','Штука'),('т','Тонна'),('кг','Килограмм'),('л','Литр'),('ч','Час'),('m','Метр (m)'),('m3','Кубический метр (m3)')]


def seed_units(apps, schema_editor):
    alias = schema_editor.connection.alias
    Unit = apps.get_model('works','MeasurementUnit')
    Company = apps.get_model('accounts','Company')
    for company_id in Company.objects.using(alias).values_list('pk',flat=True):
        for symbol, name in DEFAULTS:
            Unit.objects.using(alias).get_or_create(company_id=company_id,symbol=symbol,defaults={'name':name})
    for model_name in ('ProjectWork','ProjectWorkItem','WorkTemplate','WorkTemplateItem'):
        Model = apps.get_model('works',model_name)
        for company_id, symbol in Model.objects.using(alias).exclude(unit='').values_list('company_id','unit').distinct():
            Unit.objects.using(alias).get_or_create(company_id=company_id,symbol=symbol,defaults={'name':symbol})


class Migration(migrations.Migration):
    dependencies = [('works','0005_workgroup_projectwork_work_group_measurementunit_and_more')]
    operations = [migrations.RunPython(seed_units,migrations.RunPython.noop)]
