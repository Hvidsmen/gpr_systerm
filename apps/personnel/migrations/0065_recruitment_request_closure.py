from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


UNAVAILABLE={'REJECTED','WITHDRAWN','NOT_REQUIRED','NO_SHOW','RESERVE'}


def backfill_closure(apps,schema_editor):
    Request=apps.get_model('personnel','RecruitmentRequest')
    Candidate=apps.get_model('personnel','RecruitmentCandidate')
    now=timezone.now()
    for row in Request.objects.filter(cancelled_at__isnull=True).iterator(chunk_size=200):
        found=Candidate.objects.filter(request_id=row.pk).exclude(status__in=UNAVAILABLE).count()
        if row.completed_at or found>=row.quantity:
            closed_at=row.completed_at or now
            reason=('Требуемое количество сотрудников фактически прибыло.' if row.completed_at else
                f'Подобрано требуемое количество сотрудников: {found} из {row.quantity}.')
            Request.objects.filter(pk=row.pk).update(closed_at=closed_at,close_kind='AUTO',close_reason=reason)


class Migration(migrations.Migration):

    dependencies=[('personnel','0064_normalize_recruitment_approval_stages')]

    operations=[
        migrations.AddField(model_name='recruitmentrequest',name='close_kind',
            field=models.CharField(blank=True,choices=[('AUTO','Автоматически'),('FORCED','Принудительно')],max_length=8,verbose_name='Способ закрытия')),
        migrations.AddField(model_name='recruitmentrequest',name='close_reason',
            field=models.TextField(blank=True,verbose_name='Причина закрытия')),
        migrations.AddField(model_name='recruitmentrequest',name='closed_at',
            field=models.DateTimeField(blank=True,db_index=True,null=True,verbose_name='Подбор закрыт')),
        migrations.AddField(model_name='recruitmentrequest',name='closed_by',
            field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,
                related_name='closed_recruitment_requests',to=settings.AUTH_USER_MODEL,verbose_name='Кто закрыл подбор')),
        migrations.RunPython(backfill_closure,migrations.RunPython.noop),
    ]
