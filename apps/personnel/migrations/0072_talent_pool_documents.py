import apps.personnel.recruitment_models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0071_talent_pool_stage_event_system_actor'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TalentPoolDocument',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('kind', models.CharField(choices=[('BASE', 'Базовый пакет документов'), ('MANAGER', 'Согласование Заказчика'), ('SECURITY', 'Согласование СЭБ')], db_index=True, max_length=12, verbose_name='Раздел документов')),
                ('file', models.FileField(storage=apps.personnel.recruitment_models.resume_storage, upload_to=apps.personnel.recruitment_models.resume_path)),
                ('original_name', models.CharField(max_length=255)),
                ('size', models.PositiveBigIntegerField()),
                ('candidate', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='documents', to='personnel.talentpoolcandidate')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='accounts.company', verbose_name='company')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='talent_pool_documents', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['kind', 'created_at', 'pk']},
        ),
    ]
