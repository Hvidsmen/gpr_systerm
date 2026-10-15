from django.urls import path
from . import views
app_name='estimates'
urlpatterns=[path('',views.estimate_list,name='estimate_list'),path('import/',views.estimate_import,name='estimate_import'),path('catalog/',views.catalog_list,name='catalog_list'),path('export/',views.estimate_export,name='estimate_export'),path('<int:pk>/',views.estimate_detail,name='estimate_detail')]
