from django.conf import settings
from django.db import migrations,models
import django.db.models.deletion


def link_existing_names(apps,schema_editor):
    Candidate=apps.get_model('personnel','TalentPoolCandidate')
    Specialist=apps.get_model('personnel','RecruitmentSpecialist')
    for candidate in Candidate.objects.all().iterator(chunk_size=500):
        name=' '.join(str((candidate.source_data or {}).get('responsible_hr') or '').split())
        if not name:continue
        specialist=Specialist.objects.filter(company_id=candidate.company_id,name__iexact=name).first()
        if specialist is None:
            specialist=Specialist.objects.create(company_id=candidate.company_id,name=name)
        Candidate.objects.filter(pk=candidate.pk).update(responsible_hr_id=specialist.pk)


def unlink_existing_names(apps,schema_editor):
    apps.get_model('personnel','TalentPoolCandidate').objects.update(responsible_hr_id=None)
    apps.get_model('personnel','RecruitmentSpecialist').objects.all().delete()


class Migration(migrations.Migration):
    dependencies=[('personnel','0046_talentpoolstageevent'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(
            name='RecruitmentSpecialist',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
                ('name',models.CharField(max_length=250,verbose_name='Ответственный HR')),
                ('is_active',models.BooleanField(default=True,verbose_name='Доступен для выбора')),
                ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
                ('user',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='recruitment_specialist_profiles',to=settings.AUTH_USER_MODEL,verbose_name='Пользователь портала')),
            ],
            options={
                'ordering':['name','pk'],
                'constraints':[models.UniqueConstraint(fields=('company','name'),name='personnel_unique_recruitment_specialist')],
            },
        ),
        migrations.AddField(
            model_name='talentpoolcandidate',name='responsible_hr',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='candidates',to='personnel.recruitmentspecialist',verbose_name='Ответственный HR'),
        ),
        migrations.RunPython(link_existing_names,unlink_existing_names),
    ]
