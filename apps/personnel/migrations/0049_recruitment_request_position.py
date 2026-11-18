from django.db import migrations,models
import django.db.models.deletion


def link_positions(apps,schema_editor):
    Request=apps.get_model('personnel','RecruitmentRequest')
    Position=apps.get_model('resources','Position')
    for request in Request.objects.all().iterator(chunk_size=500):
        name=' '.join((request.position or '').split())
        if not name:continue
        matches=list(Position.objects.filter(company_id=request.company_id,name__iexact=name).values_list('pk',flat=True)[:2])
        if len(matches)==1:Request.objects.filter(pk=request.pk).update(position_ref_id=matches[0],position=name)


class Migration(migrations.Migration):
    dependencies=[('personnel','0048_structured_talent_pool_profile'),('resources','0030_backfill_linked_employee_phones')]
    operations=[
        migrations.AddField(
            model_name='recruitmentrequest',name='position_ref',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,
                related_name='recruitment_requests',to='resources.position',verbose_name='Должность из справочника'),
        ),
        migrations.RunPython(link_positions,migrations.RunPython.noop),
    ]
