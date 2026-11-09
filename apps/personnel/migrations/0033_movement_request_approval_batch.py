from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0032_movement_request_cancellation')]
    operations=[migrations.AddField(model_name='movementreviewrequest',name='approval_batch',field=models.UUIDField(blank=True,db_index=True,null=True))]
