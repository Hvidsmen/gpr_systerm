import django.db.models.deletion
from django.db import migrations, models


def backfill_objects(apps, schema_editor):
    Request=apps.get_model('personnel','MovementReviewRequest')
    Object=apps.get_model('projects','ConstructionObject')
    Link=apps.get_model('personnel','CampObjectLink')
    for row in Request.objects.all().iterator():
        site=Object.objects.filter(company_id=row.company_id,name__iexact=(row.site or '').strip()).order_by('pk').first()
        if not site:
            camp_id=row.to_camp_id or row.from_camp_id
            link=Link.objects.filter(company_id=row.company_id,camp_id=camp_id).select_related('construction_object').order_by('construction_object__name','pk').first()
            site=link.construction_object if link else None
        if site:
            row.site_object_id=site.pk
            row.construction_object_id=site.parent_id or site.pk
            row.site=site.name
            row.save(update_fields=['construction_object','site_object','site'])


class Migration(migrations.Migration):
    dependencies=[('personnel','0022_base_city_and_planning_position')]
    operations=[
        migrations.AddField(model_name='movementreviewrequest',name='construction_object',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='movement_review_object_requests',to='projects.constructionobject',verbose_name='Объект')),
        migrations.AddField(model_name='movementreviewrequest',name='site_object',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,related_name='movement_review_site_requests',to='projects.constructionobject',verbose_name='Участок')),
        migrations.RunPython(backfill_objects,migrations.RunPython.noop),
    ]
