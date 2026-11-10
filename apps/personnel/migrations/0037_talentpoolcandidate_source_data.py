from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[('personnel','0036_backfill_portal_movement_request_numbers')]
    operations=[migrations.AddField(model_name='talentpoolcandidate',name='source_data',field=models.JSONField(blank=True,default=dict,verbose_name='Данные источника'))]
