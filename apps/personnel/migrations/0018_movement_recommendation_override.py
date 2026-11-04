from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('accounts','0029_transport_dispatcher_role'),('personnel','0017_assignment_cancellation')]
    operations=[
        migrations.CreateModel(
            name='MovementRecommendationOverride',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('kind',models.CharField(choices=[('ARRIVAL','Заезд'),('DEPARTURE','Выезд')],max_length=12,verbose_name='Направление')),
                ('planned_on',models.DateField(verbose_name='Рекомендуемая дата')),
                ('action',models.CharField(choices=[('ADD','Добавить'),('EXCLUDE','Исключить')],max_length=8,verbose_name='Изменение')),
                ('camp',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='movement_recommendation_overrides',to='personnel.camp')),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
                ('person',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='movement_recommendation_overrides',to='personnel.person')),
                ('recorded_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering':['planned_on','person__full_name','pk']},
        ),
        migrations.AddConstraint(model_name='movementrecommendationoverride',constraint=models.UniqueConstraint(fields=('company','person','camp','kind','planned_on'),name='personnel_unique_movement_recommendation_override')),
    ]
