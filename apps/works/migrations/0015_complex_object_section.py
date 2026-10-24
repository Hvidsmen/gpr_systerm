from collections import Counter
from django.db import migrations, models
import django.db.models.deletion


def backfill(apps, schema_editor):
    ComplexObject=apps.get_model('works','ComplexObject')
    Work=apps.get_model('works','ProjectWork')
    Section=apps.get_model('projects','Section')
    using=schema_editor.connection.alias
    for obj in ComplexObject.objects.using(using).order_by('pk').iterator():
        works=list(Work.objects.using(using).filter(complex_object_id=obj.pk).order_by('pk'))
        valid_sections=set(Section.objects.using(using).filter(company_id=obj.company_id,construction_object_id=obj.construction_object_id).values_list('pk',flat=True))
        if any(w.company_id!=obj.company_id or w.section_id not in valid_sections for w in works):
            raise RuntimeError(f'Complex object {obj.pk}: inconsistent company/object references; migration cancelled.')
        counts=Counter(w.section_id for w in works)
        section_id=min(counts,key=lambda pk:(-counts[pk],pk)) if counts else min(valid_sections,default=None)
        if section_id is None:
            code=f'KO-{obj.pk}'
            index=0
            while Section.objects.using(using).filter(construction_object_id=obj.construction_object_id,code=code).exists():
                index+=1;code=f'KO-{obj.pk}-{index}'
            section_id=Section.objects.using(using).create(company_id=obj.company_id,construction_object_id=obj.construction_object_id,name='Комплексные объекты',code=code).pk
        used=set(Work.objects.using(using).filter(section_id=section_id).values_list('code',flat=True))
        for work in works:
            if work.section_id==section_id:continue
            code=work.code
            if code in used:
                code=f'KO-{obj.pk}-{work.pk}'
                while code in used:code+='x'
            Work.objects.using(using).filter(pk=work.pk).update(section_id=section_id,code=code)
            used.add(code)
        ComplexObject.objects.using(using).filter(pk=obj.pk).update(section_id=section_id)


class Migration(migrations.Migration):
    dependencies=[('works','0014_schedule_catalogue_fields'),('projects','0003_section_description')]
    operations=[
        migrations.AddField(model_name='complexobject',name='section',field=models.ForeignKey(to='projects.section',null=True,on_delete=django.db.models.deletion.PROTECT,related_name='complex_objects',verbose_name='Раздел')),
        migrations.RunPython(backfill,migrations.RunPython.noop),
        migrations.AlterField(model_name='complexobject',name='section',field=models.ForeignKey(to='projects.section',on_delete=django.db.models.deletion.PROTECT,related_name='complex_objects',verbose_name='Раздел')),
    ]
