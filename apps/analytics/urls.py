from django.urls import path
from . import views
from .revenue_views import RevenueDashboard, RevenueExport

app_name = 'dashboard'

urlpatterns = [
    path('revenue/', RevenueDashboard.as_view(), name='revenue'),
    path('revenue/export/', RevenueExport.as_view(), name='revenue_export'),
    path('', views.dashboard_view, name='index'),
]