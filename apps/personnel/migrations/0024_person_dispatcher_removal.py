from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('personnel','0023_movement_request_object_and_site'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.AddField(model_name='person',name='dispatcher_removal_reason',field=models.TextField(blank=True,verbose_name='Причина удаления из диспетчерской')),
        migrations.AddField(model_name='person',name='dispatcher_removed_at',field=models.DateTimeField(blank=True,null=True,verbose_name='Удалён из диспетчерской')),
        migrations.AddField(model_name='person',name='dispatcher_removed_by',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL,verbose_name='Удалил из диспетчерской')),
    ]
