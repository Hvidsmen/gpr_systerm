import django.db.models.deletion
from django.db import migrations,models


def rename_tasks(apps,schema_editor):
    Task=apps.get_model('planning','WorkTask')
    for task in Task.objects.filter(key__startswith='personnel-departure-approval:'):
        task.key=task.key.replace('personnel-departure-approval:','personnel-movement-approval:',1);task.save(update_fields=['key'])


class Migration(migrations.Migration):
    dependencies=[('personnel','0029_alter_movementreviewevent_company_and_more'),('planning','0053_alter_worktask_kind')]
    operations=[
        migrations.AlterField(model_name='movementreviewrequest',name='status',field=models.CharField(choices=[('OPEN','Ожидает проверки'),('PENDING_APPROVAL','На согласовании'),('RETURNED','Возвращено на доработку'),('PEREVAKHTA','Передано в перевахту'),('AT_HUB','В ОП Дудинка'),('EN_ROUTE','В пути из ОП Дудинка'),('COMPLETED','Завершено'),('ACCEPTED','Принято')],default='OPEN',max_length=20,verbose_name='Статус')),
        migrations.AddField(model_name='movementreviewrequest',name='final_arrived_on',field=models.DateField(blank=True,null=True,verbose_name='Прибытие в ВЖГ')),
        migrations.AddField(model_name='arrivalpreparation',name='movement_request',field=models.OneToOneField(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='recruitment_arrival',to='personnel.movementreviewrequest')),
        migrations.RunPython(rename_tasks,migrations.RunPython.noop),
    ]
