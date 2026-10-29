from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0019_employees_import_schedule')]
    operations = [
        migrations.AddField(model_name='onecconnection', name='positions_schedule',
                            field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name='onecconnection', name='employees_schedule',
                            field=models.JSONField(default=dict, blank=True)),
    ]
