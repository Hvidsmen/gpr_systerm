from django.urls import path
from . import views
app_name='estimates'
urlpatterns=[path('report/',views.estimate_report_list,name='estimate_report_list'),path('<int:pk>/delete/',views.estimate_delete,name='estimate_delete'),path('',views.estimate_list,name='estimate_list'),path('import/',views.estimate_import,name='estimate_import'),path('catalog/',views.catalog_list,name='catalog_list'),path('export/',views.estimate_export,name='estimate_export'),path('<int:pk>/',views.estimate_detail,name='estimate_detail')]
