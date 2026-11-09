import django.db.models.deletion
from django.conf import settings
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('personnel','0030_arrival_workflow')]
    operations=[
        migrations.CreateModel(name='TalentPoolCandidate',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),('full_name',models.CharField(max_length=250,verbose_name='ФИО')),('phone',models.CharField(blank=True,max_length=80,verbose_name='Телефон')),('notes',models.TextField(blank=True,verbose_name='Комментарий')),('is_active',models.BooleanField(default=True,verbose_name='Доступен для вакансий')),('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),('created_by',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='talent_pool_candidates',to=settings.AUTH_USER_MODEL))],options={'ordering':['full_name','pk']}),
        migrations.AddField(model_name='recruitmentcandidate',name='pool_candidate',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='vacancy_candidates',to='personnel.talentpoolcandidate',verbose_name='Кандидат из пула')),
    ]
