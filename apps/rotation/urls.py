from django.urls import path
from . import views
app_name = 'rotation'
urlpatterns = [
    path('<int:pk>/excel/', views.PlanExport.as_view(), name='plan_export'),
    path('people/<int:pk>/status/', views.StatusEdit.as_view(), name='status_update'),
    path('', views.PlanList.as_view(), name='plan_list'),
    path('create/', views.PlanCreate.as_view(), name='plan_create'),
    path('<int:pk>/', views.PlanDetail.as_view(), name='plan_detail'),
    path('positions/<int:pk>/', views.RoleEdit.as_view(), name='role_update'),
    path('positions/<int:pk>/people/add/', views.PersonEdit.as_view(), name='person_create'),
    path('people/<int:pk>/', views.PersonEdit.as_view(), name='person_update'),
    path('people/<int:pk>/delete/', views.PersonDelete.as_view(), name='person_delete'),
]
