from core.permissions import role_code, DISPATCHER_ROLES


def staging_portal(request):
    from django.conf import settings
    user = request.user
    url = getattr(settings, 'STAGING_PORTAL_URL', '')
    login = getattr(settings, 'STAGING_PORTAL_OWNER_LOGIN', '').casefold()
    if (not url or not login or getattr(settings, 'STAGING_MODE', False) or
            getattr(settings, 'TRAINING_MODE', False) or getattr(request, 'acting_admin', None) or
            not user.is_authenticated or not user.is_active or not user.company_id or
            user.archived_at is not None or role_code(user) != 'ADMIN'):
        return {}
    identity = user.ldap_login if user.auth_source == 'LDAP' else user.username
    if identity.casefold() != login:
        return {}
    # Match the owner unambiguously across local and Windows identities.
    from django.db.models import Q
    from django.contrib.auth import get_user_model
    owners = list(get_user_model().objects.filter(
        Q(auth_source='LOCAL', username__iexact=login) | Q(auth_source='LDAP', ldap_login__iexact=login),
        is_active=True, archived_at__isnull=True, company__isnull=False,
    ).values_list('pk', flat=True)[:2])
    return {'staging_portal_url': url} if owners == [user.pk] else {}


def planning_tasks(request):
    user=request.user
    if not user.is_authenticated or not user.company_id:return {}
    role=role_code(user);result={'catalogue_menu_hidden':role in DISPATCHER_ROLES | {'COMMANDANT'}}
    from .permissions import ROLES, CATALOG_MANAGER_ROLES
    if role in CATALOG_MANAGER_ROLES | {'ADMIN','PLANNER','MANAGER'}:
        from apps.resources.catalogue_review_views import queue_for
        result['catalogue_review_access'] = True
        result['catalogue_review_count'] = queue_for(user).filter(status='PENDING').count()
    if role in CATALOG_MANAGER_ROLES:
        from apps.planning.task_services import visible,filter_scope
        result.update(task_inbox_access=True,task_inbox_count=filter_scope(visible(user),user,'all' if role_code(user)=='ADMIN' else 'incoming').filter(status__in=['OPEN','IN_REVIEW']).count())
    if role in ROLES - CATALOG_MANAGER_ROLES:
        from apps.planning.task_services import generate_daily,visible,filter_scope
        generate_daily(user)
        result.update(task_inbox_access=True,task_inbox_count=filter_scope(visible(user),user,'all' if role_code(user)=='ADMIN' else 'incoming').filter(status__in=['OPEN','IN_REVIEW']).count())
    if role in {'ADMIN','PLANNER','ESTIMATOR'}:
        from apps.planning.models import EstimateReviewTask
        reviews=EstimateReviewTask.objects.filter(company_id=user.company_id,status='OPEN')
        if role=='ESTIMATOR':reviews=reviews.filter(assigned_to=user)
        result.update(estimate_review_access=True,estimate_reviews_due=reviews.count())
    return result
