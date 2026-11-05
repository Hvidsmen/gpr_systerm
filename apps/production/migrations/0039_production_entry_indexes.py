from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('production', '0038_operational_work_facts')]
    operations = [
        migrations.AddIndex(
            model_name='dailyfact',
            index=models.Index(fields=['company', 'date', 'project_work'], name='prod_fact_company_day_work'),
        ),
        migrations.AddIndex(
            model_name='employeeactivity',
            index=models.Index(fields=['company', 'construction_object', 'date', 'entry_direction'], name='prod_emp_obj_day_direction'),
        ),
        migrations.AddIndex(
            model_name='employeeworklink',
            index=models.Index(fields=['company', 'activity', 'fact'], name='prod_link_activity_fact'),
        ),
    ]
