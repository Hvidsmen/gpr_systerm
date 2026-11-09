import django.db.models.deletion
from django.conf import settings
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0031_talent_pool'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.AlterField(model_name='movementreviewrequest',name='status',field=models.CharField(choices=[('OPEN','Ожидает проверки'),('PENDING_APPROVAL','На согласовании'),('RETURNED','Возвращено на доработку'),('PEREVAKHTA','Передано в перевахту'),('AT_HUB','В ОП Дудинка'),('EN_ROUTE','В пути из ОП Дудинка'),('COMPLETED','Завершено'),('ACCEPTED','Принято'),('CANCELLED','Отменено')],default='OPEN',max_length=20,verbose_name='Статус')),
        migrations.AddField(model_name='movementreviewrequest',name='cancellation_reason',field=models.TextField(blank=True,verbose_name='Причина отмены')),
        migrations.AddField(model_name='movementreviewrequest',name='cancelled_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.AddField(model_name='movementreviewrequest',name='cancelled_by',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
    ]
