from django.db import migrations,models

class Migration(migrations.Migration):
    dependencies=[('personnel','0034_movement_planned_hub_departure')]
    operations=[migrations.AddField(model_name='movementreviewrequest',name='approval_route',field=models.JSONField(blank=True,default=dict,verbose_name='Маршрут согласования'))]
