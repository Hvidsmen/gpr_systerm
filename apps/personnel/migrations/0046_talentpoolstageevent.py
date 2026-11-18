from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion


STAGES=[
    ('NEW','Новый'),('SCREENING','На рассмотрении'),('HR_INTERVIEW','Собеседование HR'),
    ('MANAGER_APPROVAL','Согласование руководителя'),('SECURITY_CHECK','Проверка СБ'),
    ('MEDICAL_DOCUMENTS','Медосмотр и документы'),('SELECTED','Отобран'),
    ('READY','Готов к заезду'),('TRANSFERRED','Поставлен на заезд'),
    ('ARRIVAL_APPROVED','Заезд согласован'),('ARRIVED','Заехал'),('RESERVE','Резерв'),
    ('REJECTED','Отказ работодателя'),('WITHDRAWN','Отказ кандидата'),
    ('NOT_REQUIRED','Не потребовался'),('NO_SHOW','Не заехал'),
]


class Migration(migrations.Migration):
    dependencies=[('personnel','0045_recruitment_funnel'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(
            name='TalentPoolStageEvent',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('from_stage',models.CharField(choices=STAGES,max_length=24,verbose_name='Предыдущий этап')),
                ('to_stage',models.CharField(choices=STAGES,max_length=24,verbose_name='Новый этап')),
                ('note',models.TextField(blank=True,verbose_name='Комментарий')),
                ('actor',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='talent_pool_stage_events',to=settings.AUTH_USER_MODEL)),
                ('candidate',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='stage_events',to='personnel.talentpoolcandidate')),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
            ],
            options={'ordering':['-created_at','-pk']},
        ),
    ]
