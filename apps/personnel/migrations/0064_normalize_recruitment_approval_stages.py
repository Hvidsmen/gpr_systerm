from django.db import migrations
from django.utils import timezone


FLOW={'MANAGER_APPROVAL','SECURITY_CHECK','MEDICAL_DOCUMENTS','PASS_PROCESSING','READY'}


def normalized(row):
    if not row.manager_approved:return 'MANAGER_APPROVAL'
    if not row.security_approved:return 'SECURITY_CHECK'
    if not row.medical_exam_passed:return 'MEDICAL_DOCUMENTS'
    if not row.pass_documents_sent or not row.pass_ready:return 'PASS_PROCESSING'
    return 'READY'


def normalize_stages(apps,schema_editor):
    Candidate=apps.get_model('personnel','TalentPoolCandidate')
    now=timezone.now()
    for row in Candidate.objects.filter(stage__in=FLOW).iterator(chunk_size=200):
        stage=normalized(row)
        if stage!=row.stage:
            Candidate.objects.filter(pk=row.pk).update(stage=stage,stage_started_at=now)


class Migration(migrations.Migration):

    dependencies=[('personnel','0063_recruitment_pass_dates')]

    operations=[migrations.RunPython(normalize_stages,migrations.RunPython.noop)]
