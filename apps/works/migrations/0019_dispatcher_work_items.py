from django.db import migrations, models


TARGET_WORKS = {'эмр. прочее', 'вжг. прочее'}


def prepare_other_complexes(apps, schema_editor):
    Section = apps.get_model('projects', 'Section')
    ComplexObject = apps.get_model('works', 'ComplexObject')
    ProjectWork = apps.get_model('works', 'ProjectWork')
    for section in Section.objects.filter(name__iexact='Прочее').iterator():
        complex_object, created = ComplexObject.objects.get_or_create(
            company_id=section.company_id,
            construction_object_id=section.construction_object_id,
            name='Прочее',
            defaults={
                'section_id': section.pk,
                'description': 'Оперативные работы вне СМГ, создаваемые диспетчером производства.',
                'allow_dispatcher_work_items': True,
            },
        )
        if complex_object.section_id != section.pk:
            continue
        if not complex_object.allow_dispatcher_work_items:
            ComplexObject.objects.filter(pk=complex_object.pk).update(allow_dispatcher_work_items=True)
        candidates = ProjectWork.objects.filter(
            company_id=section.company_id,
            section_id=section.pk,
            kind='SIMPLE',
        )
        ids = [work.pk for work in candidates if ' '.join(work.name.split()).casefold() in TARGET_WORKS]
        ProjectWork.objects.filter(pk__in=ids).update(kind='COMPOSITE', complex_object_id=complex_object.pk)


class Migration(migrations.Migration):
    dependencies = [('works', '0018_projectwork_is_legacy_structure_and_more')]
    operations = [
        migrations.AddField(
            model_name='complexobject',
            name='allow_dispatcher_work_items',
            field=models.BooleanField(
                default=False,
                help_text='Действует только для работ вне СМГ. Диспетчер сможет создавать работы внутри комплексов этого объекта.',
                verbose_name='Разрешить диспетчеру добавлять работы при вводе факта',
            ),
        ),
        migrations.RunPython(prepare_other_complexes, migrations.RunPython.noop),
    ]
