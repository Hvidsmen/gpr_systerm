from django.db import migrations,models


STAGES=[
    ('NEW','Новый'),('SCREENING','На рассмотрении'),('HR_INTERVIEW','Собеседование HR'),
    ('MANAGER_APPROVAL','Согласование руководителя'),('SECURITY_CHECK','Проверка СБ'),
    ('MEDICAL_DOCUMENTS','Медосмотр и документы'),('SELECTED','Отобран'),
    ('READY','Готов к заезду'),('TRANSFERRED','Поставлен на заезд'),
    ('ARRIVAL_APPROVED','Заезд согласован'),('ARRIVED','Заехал'),('RESERVE','Резерв'),
    ('REJECTED','Отказ работодателя'),('WITHDRAWN','Отказ кандидата'),
    ('NOT_REQUIRED','Не потребовался'),('NO_SHOW','Не заехал'),
]


def normalized(value):return ' '.join((value or '').split()).casefold()
def phone(value):return ''.join(character for character in (value or '') if character.isdigit())


def unify_candidates(apps,schema_editor):
    Pool=apps.get_model('personnel','TalentPoolCandidate')
    Candidate=apps.get_model('personnel','RecruitmentCandidate')
    pools=list(Pool.objects.select_related(None).all())
    by_company_name={}
    by_company_identity={}
    for row in pools:
        source=row.source_data or {}
        row.stage='SELECTED' if source.get('number') or source.get('duplicate_name') else 'NEW'
        row.save(update_fields=['stage'])
        name=normalized(row.full_name);identity=(row.company_id,name,phone(row.phone))
        by_company_name.setdefault((row.company_id,name),[]).append(row)
        by_company_identity[identity]=row
    for candidate in Candidate.objects.select_related('request').order_by('created_at','pk'):
        pool=candidate.pool_candidate if candidate.pool_candidate_id else None
        if pool is None:
            identity=(candidate.company_id,normalized(candidate.full_name),phone(candidate.phone))
            pool=by_company_identity.get(identity)
            if pool is None and not identity[2]:
                matches=by_company_name.get(identity[:2],[])
                pool=matches[0] if len(matches)==1 else None
            if pool is None:
                pool=Pool.objects.create(company_id=candidate.company_id,full_name=candidate.full_name,
                    phone=candidate.phone,notes=candidate.notes,source_data={
                        'position':candidate.request.position,
                        'construction_site':candidate.request.construction_object.name,
                        'origin':'existing_recruitment',
                    },stage=candidate.status,created_by_id=candidate.created_by_id)
                pools.append(pool);by_company_name.setdefault(identity[:2],[]).append(pool);by_company_identity[identity]=pool
            candidate.pool_candidate_id=pool.pk
        candidate.save(update_fields=['status','pool_candidate'])
        pool.stage=candidate.status
        pool.save(update_fields=['stage'])


class Migration(migrations.Migration):
    dependencies=[('personnel','0044_movementreviewrequest_ticket_details')]
    operations=[
        migrations.AddField(model_name='talentpoolcandidate',name='stage',
            field=models.CharField(choices=STAGES,default='NEW',max_length=24,verbose_name='Этап воронки')),
        migrations.AlterField(model_name='recruitmentcandidate',name='status',
            field=models.CharField(choices=STAGES,default='NEW',max_length=24,verbose_name='Статус')),
        migrations.RunPython(unify_candidates,migrations.RunPython.noop),
    ]
