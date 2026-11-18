from datetime import datetime
from django.db import migrations,models
import django.db.models.deletion


def parsed_date(value):
    if not value:return None
    text=str(value).strip()[:10]
    for pattern in ('%Y-%m-%d','%d.%m.%Y'):
        try:return datetime.strptime(text,pattern).date()
        except ValueError:pass
    return None


def unique_match(model,company_id,name):
    name=' '.join(str(name or '').split())
    if not name:return None
    matches=list(model.objects.filter(company_id=company_id,name__iexact=name).values_list('pk',flat=True)[:2])
    return matches[0] if len(matches)==1 else None


def first_date(source,*keys):
    values=[]
    for key in keys:
        value=parsed_date(source.get(key))
        if value:values.append(value)
    for event in source.get('funnel_sources') or []:
        data=event.get('data') or {}
        for key in keys:
            value=parsed_date(data.get(key))
            if value:values.append(value)
    return max(values) if values else None


def structure_existing_profiles(apps,schema_editor):
    Candidate=apps.get_model('personnel','TalentPoolCandidate')
    BaseCity=apps.get_model('personnel','BaseCity')
    Position=apps.get_model('resources','Position')
    ConstructionObject=apps.get_model('projects','ConstructionObject')
    for candidate in Candidate.objects.all().iterator(chunk_size=500):
        source=candidate.source_data or {}
        ready_on=first_date(source,'ready_for_arrival_on','planned_mobilization','readiness')
        manager_on=first_date(source,'manager_approved_on','manager_approval','customer_approval')
        security_on=first_date(source,'security_approved_on','security_approval','security_result')
        medical_on=first_date(source,'medical_exam_on','medical_exam','medical_result')
        Candidate.objects.filter(pk=candidate.pk).update(
            position_id=unique_match(Position,candidate.company_id,source.get('position')),
            construction_object_id=unique_match(ConstructionObject,candidate.company_id,source.get('construction_site')),
            base_city_id=unique_match(BaseCity,candidate.company_id,source.get('base_city')),
            ready_for_arrival=bool(ready_on),ready_for_arrival_on=ready_on,
            manager_approved=bool(manager_on),manager_approved_on=manager_on,
            security_approved=bool(security_on),security_approved_on=security_on,
            medical_exam_passed=bool(medical_on),medical_exam_on=medical_on,
        )


class Migration(migrations.Migration):
    dependencies=[('personnel','0047_recruitment_specialists'),('projects','0003_section_description'),('resources','0030_backfill_linked_employee_phones')]
    operations=[
        migrations.AddField(model_name='talentpoolcandidate',name='position',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='talent_pool_candidates',to='resources.position',verbose_name='Должность')),
        migrations.AddField(model_name='talentpoolcandidate',name='construction_object',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='talent_pool_candidates',to='projects.constructionobject',verbose_name='Объект / проект')),
        migrations.AddField(model_name='talentpoolcandidate',name='camp',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='talent_pool_candidates',to='personnel.camp',verbose_name='ВЖГ')),
        migrations.AddField(model_name='talentpoolcandidate',name='base_city',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='talent_pool_candidates',to='personnel.basecity',verbose_name='Базовый город')),
        migrations.AddField(model_name='talentpoolcandidate',name='ready_for_arrival',field=models.BooleanField(default=False,verbose_name='Готов к заезду')),
        migrations.AddField(model_name='talentpoolcandidate',name='ready_for_arrival_on',field=models.DateField(blank=True,null=True,verbose_name='Дата готовности к заезду')),
        migrations.AddField(model_name='talentpoolcandidate',name='manager_approved',field=models.BooleanField(default=False,verbose_name='Согласован непосредственным руководителем')),
        migrations.AddField(model_name='talentpoolcandidate',name='manager_approved_on',field=models.DateField(blank=True,null=True,verbose_name='Дата согласования руководителем')),
        migrations.AddField(model_name='talentpoolcandidate',name='security_approved',field=models.BooleanField(default=False,verbose_name='Согласован СБ')),
        migrations.AddField(model_name='talentpoolcandidate',name='security_approved_on',field=models.DateField(blank=True,null=True,verbose_name='Дата согласования СБ')),
        migrations.AddField(model_name='talentpoolcandidate',name='medical_exam_passed',field=models.BooleanField(default=False,verbose_name='Медицинский осмотр пройден')),
        migrations.AddField(model_name='talentpoolcandidate',name='medical_exam_on',field=models.DateField(blank=True,null=True,verbose_name='Дата медицинского осмотра')),
        migrations.RunPython(structure_existing_profiles,migrations.RunPython.noop),
    ]
