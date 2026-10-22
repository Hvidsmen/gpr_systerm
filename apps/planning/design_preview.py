"""An isolated design proposal with demo data; never reads or edits plan records."""
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views import View
from core.permissions import READ_ROLES, require_roles


class DesignPreview(View):
    def get(self, request):
        require_roles(request.user, READ_ROLES)
        return HttpResponse(render_to_string('planning/design_preview.html'))
