from django.db import migrations


def archive_arrived(apps,schema_editor):
    Pool=apps.get_model('personnel','TalentPoolCandidate')
    Pool.objects.filter(is_active=True,vacancy_candidates__arrival__status='ARRIVED',
        vacancy_candidates__arrival__actual_stay__isnull=False,
        vacancy_candidates__arrival__actual_stay__cancelled_at__isnull=True).update(is_active=False)


class Migration(migrations.Migration):
    dependencies=[('personnel','0039_recruitmentrequest_completed_at')]
    operations=[migrations.RunPython(archive_arrived,migrations.RunPython.noop)]
