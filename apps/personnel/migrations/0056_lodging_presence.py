from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('personnel', '0055_alter_arrivalpreparation_camp_alter_camp_is_active_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name='lodgingcontrol',
            name='resource',
            field=models.CharField(choices=[('employee', 'Сотрудник'), ('bed', 'Место'), ('camp', 'Местонахождение')], max_length=10),
        ),
        migrations.CreateModel(
            name='LodgingPresence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('session', models.UUIDField()),
                ('expires_at', models.DateTimeField()),
                ('camp', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lodging_presence', to='personnel.camp')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='accounts.company', verbose_name='company')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'indexes': [models.Index(fields=['company', 'camp', 'expires_at'], name='lodging_presence_scope')],
                'constraints': [models.UniqueConstraint(fields=('company', 'camp', 'session'), name='unique_lodging_camp_presence')],
            },
        ),
    ]
