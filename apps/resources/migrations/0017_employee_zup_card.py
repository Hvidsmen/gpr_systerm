from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('resources', '0016_employee_zup_full_name')]
    operations = [migrations.AddField(
        model_name='employee', name='zup_card',
        field=models.JSONField('Данные карточки ЗУП', default=dict, blank=True, editable=False),
    )]
