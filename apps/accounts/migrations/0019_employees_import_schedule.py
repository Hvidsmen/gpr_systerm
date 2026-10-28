from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0018_commandant_role')]
    operations = [migrations.AddField(
        model_name='onecconnection', name='employees_interval_minutes',
        field=models.PositiveIntegerField(default=0),
    )]
