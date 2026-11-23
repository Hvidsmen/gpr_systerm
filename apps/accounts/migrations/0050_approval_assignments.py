from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0049_accessgroup_assigned_camps')]

    operations = [
        migrations.CreateModel(
            name='ApprovalAssignment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('process', models.CharField(choices=[('MOVEMENT', 'Заявки на заезд и выезд'), ('GPR', 'ГПР'), ('SMG', 'СМГ')], max_length=20, verbose_name='Процесс')),
                ('stage_code', models.CharField(max_length=50, verbose_name='Код этапа')),
                ('changed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.user')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approval_assignments', to='accounts.company')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='approval_assignments', to='accounts.user', verbose_name='Согласующий')),
            ],
            options={'ordering': ['process', 'stage_code']},
        ),
        migrations.CreateModel(
            name='ApprovalAssignmentChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField(blank=True, verbose_name='Причина замены')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('assignment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='history', to='accounts.approvalassignment')),
                ('changed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.user')),
                ('new_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.user')),
                ('previous_user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='accounts.user')),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
        migrations.CreateModel(
            name='ApprovalNotice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('process', models.CharField(choices=[('MOVEMENT', 'Заявки на заезд и выезд'), ('GPR', 'ГПР'), ('SMG', 'СМГ')], max_length=20)),
                ('stage_code', models.CharField(max_length=50)),
                ('task_id', models.PositiveBigIntegerField(db_index=True)),
                ('status', models.CharField(choices=[('PENDING', 'Ожидает отправки'), ('SENT', 'Отправлено'), ('FAILED', 'Ошибка')], default='PENDING', max_length=10)),
                ('intended_email', models.EmailField(blank=True, max_length=254)),
                ('delivered_to', models.JSONField(blank=True, default=list)),
                ('error', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='approval_notices', to='accounts.company')),
                ('intended_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='intended_approval_notices', to='accounts.user')),
            ],
            options={'ordering': ['-created_at', '-pk']},
        ),
        migrations.AddConstraint(model_name='approvalassignment', constraint=models.UniqueConstraint(fields=('company', 'process', 'stage_code'), name='unique_company_approval_assignment')),
        migrations.AddConstraint(model_name='approvalnotice', constraint=models.UniqueConstraint(fields=('company', 'process', 'stage_code', 'task_id', 'intended_user'), name='unique_approval_notice')),
    ]
