from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0027_accessgroup_production_view_objects_and_more')]
    operations = [
        migrations.RenameField(model_name='accessgroup', old_name='production_view_objects', new_name='production_additional_objects'),
        migrations.RenameField(model_name='user', old_name='production_view_objects', new_name='production_additional_objects'),
        migrations.AlterField(model_name='accessgroup', name='production_additional_objects', field=models.ManyToManyField(blank=True, related_name='production_view_groups', to='projects.constructionobject', verbose_name='Производство: дополнительные объекты без ежедневных заданий')),
        migrations.AlterField(model_name='user', name='production_additional_objects', field=models.ManyToManyField(blank=True, related_name='production_viewers', to='projects.constructionobject', verbose_name='Производство: дополнительные объекты без ежедневных заданий')),
    ]
