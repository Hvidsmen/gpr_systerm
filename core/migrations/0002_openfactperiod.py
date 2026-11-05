import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounts', '0029_transport_dispatcher_role'), ('core', '0001_initial')]
    operations = [
        migrations.CreateModel(
            name='OpenFactPeriod',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='created at')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated at')),
                ('name', models.CharField(blank=True, max_length=200, verbose_name='Название')),
                ('date_from', models.DateField(verbose_name='Начало периода')),
                ('date_to', models.DateField(verbose_name='Конец периода')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='%(class)s_set', to='accounts.company', verbose_name='company')),
            ],
            options={'verbose_name':'открытый период факта','verbose_name_plural':'открытые периоды факта','ordering':['-date_from','-date_to','-pk']},
        ),
        migrations.AddConstraint(model_name='openfactperiod',constraint=models.CheckConstraint(condition=models.Q(('date_to__gte', models.F('date_from'))),name='open_fact_period_dates_valid')),
    ]
