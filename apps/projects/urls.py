from django.urls import path
from . import views

app_name = 'projects'

urlpatterns = [
    path('', views.ProjectListView.as_view(), name='project_list'),
    path('create/', views.ProjectCreateView.as_view(), name='project_create'),
    path('<int:pk>/', views.ProjectDetailView.as_view(), name='project_detail'),
    path('<int:pk>/update/', views.ProjectUpdateView.as_view(), name='project_update'),
    path('<int:pk>/delete/', views.ProjectDeleteView.as_view(), name='project_delete'),
    path('<int:project_pk>/objects/', views.ConstructionObjectListView.as_view(), name='object_list'),
    path('<int:project_pk>/objects/create/', views.ConstructionObjectCreateView.as_view(), name='object_create'),
    path('objects/<int:pk>/edit/', views.ConstructionObjectUpdateView.as_view(), name='object_edit'),
    path('objects/<int:pk>/delete/', views.ConstructionObjectDeleteView.as_view(), name='object_delete'),
    path(
        'objects/<int:pk>/sections/',
        views.SectionListView.as_view(),
        name='section_list'
    ),
    path(
        'objects/<int:pk>/sections/create/',
        views.SectionCreateView.as_view(),
        name='section_create'
    ),
    path(
        'objects/<int:pk>/sections/<int:section_pk>/edit/',
        views.SectionUpdateView.as_view(),
        name='section_edit'
    ),
    path(
        'objects/<int:pk>/sections/<int:section_pk>/delete/',
        views.SectionDeleteView.as_view(),
        name='section_delete'
    ),
]
