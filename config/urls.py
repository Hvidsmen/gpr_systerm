from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/dashboard/', permanent=False)),
    path('accounts/', include('apps.accounts.urls')),
    path('projects/', include('apps.projects.urls')),
    path('works/', include('apps.works.urls')),
    path('planning/', include('apps.planning.urls')),
    path('production/', include('apps.production.urls')),
    path('resources/', include('apps.resources.urls')),
    path('dashboard/', include('apps.analytics.urls')),

]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])