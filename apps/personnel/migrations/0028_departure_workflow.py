import django.db.models.deletion
from django.conf import settings
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0027_fix_open_portal_movement_requests'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.AlterField(model_name='movementreviewrequest',name='status',field=models.CharField(choices=[('OPEN','Ожидает проверки'),('PENDING_APPROVAL','На согласовании'),('RETURNED','Возвращено на доработку'),('PEREVAKHTA','Передано в перевахту'),('AT_HUB','В ОП Дудинка'),('COMPLETED','Завершено'),('ACCEPTED','Принято')],default='OPEN',max_length=20)),
        migrations.AddField(model_name='movementreviewrequest',name='approved_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.AddField(model_name='movementreviewrequest',name='approved_by',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='movementreviewrequest',name='approval_comment',field=models.TextField(blank=True,verbose_name='Комментарий согласования')),
        migrations.AddField(model_name='movementreviewrequest',name='hub_arrived_on',field=models.DateField(blank=True,null=True,verbose_name='Прибытие в ОП Дудинка')),
        migrations.AddField(model_name='movementreviewrequest',name='hub_departed_on',field=models.DateField(blank=True,null=True,verbose_name='Выезд из ОП Дудинка')),
        migrations.AddField(model_name='movementreviewrequest',name='tickets_ready',field=models.BooleanField(default=False,verbose_name='Билеты приобретены')),
        migrations.AddField(model_name='movementreviewrequest',name='tickets_ready_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.AddField(model_name='movementreviewrequest',name='clearance_document',field=models.FileField(blank=True,upload_to='personnel/clearance/%Y/%m/',verbose_name='Обходной лист')),
        migrations.AddField(model_name='movementreviewrequest',name='clearance_uploaded_at',field=models.DateTimeField(blank=True,null=True)),
        migrations.CreateModel(name='MovementReviewEvent',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),('action',models.CharField(max_length=120)),('comment',models.TextField(blank=True)),('actor',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='accounts.company')),('request',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='workflow_events',to='personnel.movementreviewrequest'))],options={'ordering':['created_at','pk']}),
    ]
