"""Keep catalogue context across local work pages without accepting external redirects."""
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def return_path(request, default=None):
    value = request.POST.get('return_to') or request.GET.get('return_to', '')
    prefix = reverse('works:work_list')
    if (value.startswith(prefix) and '\\' not in value and not any(ord(c) < 32 for c in value) and not value.startswith('//') and
            url_has_allowed_host_and_scheme(value, allowed_hosts=set(), require_https=request.is_secure())):
        return value
    return default or prefix
