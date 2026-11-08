from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('production','0043_match_rules_ignore_units'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(
        name='ProductionEditLock',
        fields=[
            ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
            ('created_at',models.DateTimeField(auto_now_add=True,db_index=True,verbose_name='created at')),('updated_at',models.DateTimeField(auto_now=True,verbose_name='updated at')),
            ('date',models.DateField()),('session_key',models.UUIDField()),('expires_at',models.DateTimeField(db_index=True)),
            ('company',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,related_name='%(class)s_set',to='accounts.company',verbose_name='company')),
            ('construction_object',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to='projects.constructionobject')),
            ('user',models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,to=settings.AUTH_USER_MODEL)),
        ],
        options={'constraints':[models.UniqueConstraint(fields=('construction_object','date'),name='unique_production_edit_lock')]},
    )]
