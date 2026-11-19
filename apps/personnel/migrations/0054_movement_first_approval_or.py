from django.db import migrations


FIRST_GATE=('MANAGER','SITE_CHIEF')
ROUTE=('MANAGER','SITE_CHIEF','PEREVAKHTA_SPECIALIST','DUDINKA_HEAD','DUDINKA_SECURITY_DEPUTY',
    'CHIEF_MECHANIC','PARAMEDIC','PERSONNEL_SPECIALIST','LABOR_ECONOMIST')


def default_route():
    return {code:{'status':'PENDING' if code in FIRST_GATE else 'WAITING','comment':'','actor':'','at':''} for code in ROUTE}


def activate_or_gate(apps,schema_editor):
    Request=apps.get_model('personnel','MovementReviewRequest')
    Task=apps.get_model('planning','WorkTask')
    active=Request.objects.filter(status__in=['PENDING_APPROVAL','RETURNED'])
    for row in active.only('pk','approval_route').iterator(chunk_size=500):
        route=default_route();route.update(row.approval_route or {})
        if any(route[code].get('status')=='APPROVED' for code in FIRST_GATE):
            for code in FIRST_GATE:
                if route[code].get('status') in {'PENDING','WAITING'}:route[code]['status']='SKIPPED'
            if route['PEREVAKHTA_SPECIALIST'].get('status')=='WAITING':route['PEREVAKHTA_SPECIALIST']['status']='PENDING'
        else:
            for code in FIRST_GATE:
                if route[code].get('status')=='WAITING':route[code]['status']='PENDING'
        Request.objects.filter(pk=row.pk).update(approval_route=route)

    for task in Task.objects.filter(status='OPEN',key__startswith='personnel-movement-').only('pk','key').iterator(chunk_size=200):
        if task.key.startswith('personnel-movement-batch:'):
            rows=active.filter(approval_batch=task.key.split(':',1)[1])
        elif task.key.startswith('personnel-movement-approval:'):
            rows=active.filter(pk=task.key.rsplit(':',1)[1])
        else:
            continue
        roles=set()
        for route in rows.values_list('approval_route',flat=True):
            roles.update(code for code,data in (route or {}).items() if data.get('status')=='PENDING')
        if roles:
            target=next(iter(roles)) if len(roles)==1 else 'MOVEMENT_PARALLEL'
            Task.objects.filter(pk=task.pk).update(target_role=target,assigned_to_id=None)


class Migration(migrations.Migration):
    dependencies=[('personnel','0053_recruitment_directory_cleanup')]
    operations=[migrations.RunPython(activate_or_gate,migrations.RunPython.noop)]
