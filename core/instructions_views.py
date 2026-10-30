"""Read-only operational help. Never grants access to the described actions."""
from copy import deepcopy
from urllib.parse import urlsplit
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse,resolve,Resolver404
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET
from core.permissions import require_roles,ROLES,role_code,check_route
from django.core.exceptions import PermissionDenied
from .instructions_content import GUIDES


def allowed_return(request):
    value=request.GET.get('return_to','')
    if not value.startswith('/') or value.startswith('//') or '\\' in value or any(ord(c)<32 for c in value):return ''
    if not url_has_allowed_host_and_scheme(value,allowed_hosts=set(),require_https=request.is_secure()):return ''
    try:
        match=resolve(urlsplit(value).path)
        if match.namespace not in {'production','personnel'}:return ''
        check_route(request.user,match,'GET')
    except (Resolver404,PermissionDenied,ValueError):return ''
    return value


@login_required
@require_GET
def index(request):
    require_roles(request.user,ROLES)
    query=request.GET.get('q','').strip()[:200]
    stems={'заселить':'засел','выселить':'высел','переселить':'пересел','перевести':'перев','списать':'спис','сохранить':'сохран'}
    terms=[stems.get(term,term) for term in query.casefold().split()]
    def matches(guide):
        # Titles, section names, action labels and examples are all searchable.
        return all(term in str(guide).casefold() for term in terms)
    guides=[g for g in GUIDES if matches(g)]
    recommended=next((g for g in GUIDES if g['kind']=='role' and g['role']==role_code(request.user)),None)
    return render(request,'instructions/index.html',{'section_guides':[g for g in guides if g['kind']=='section'],'role_guides':[g for g in guides if g['kind']=='role'],'recommended':recommended,'query':query,'result_count':len(guides)})


@login_required
@require_GET
def article(request,slug):
    require_roles(request.user,ROLES)
    guide=next((deepcopy(g) for g in GUIDES if g['slug']==slug),None)
    if guide is None:raise Http404('Инструкция не найдена.')
    related=[g for g in GUIDES if g['slug'] in guide.get('related',[])]
    if guide['kind']=='section':related=[g for g in GUIDES if g['kind']=='role' and guide['slug'] in g.get('related',[])]
    return render(request,'instructions/article.html',{'guide':guide,'related':related,'return_to':allowed_return(request)})
