from django.urls import path
from . import views
from .access_groups import groups
from .directory_groups import group_search
from .directory_admin import add_from_ad
from . import impersonation_views
from .one_c import integration, table_structure
from .one_c_jobs import jobs
from .user_archive import bulk_archive

app_name = 'accounts'

urlpatterns = [
    path('users/archive/', bulk_archive, name='user_bulk_archive'),
    path('integrations/1c/jobs/', jobs, name='one_c_jobs'),
    path('integrations/1c/', integration, name='one_c'),
    path('integrations/1c/tables/<str:schema>/<str:table>/', table_structure, name='one_c_table'),
    path("ad/groups/", group_search, name="ad_group_search"),
    path("access-groups/", groups, name="access_groups"),
    path("access-groups/<int:pk>/", groups, name="access_group_edit"),
    path("view-as/history/", impersonation_views.history, name="impersonation_history"),
    path("view-as/<int:pk>/", impersonation_views.start, name="impersonation_start"),
    path("view-as/stop/", impersonation_views.stop, name="impersonation_stop"),
    path("users/add-from-ad/", add_from_ad, name="add_from_ad"),
    path("users/create/", views.CompanyUserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/access/", views.CompanyUserAccessView.as_view(), name="user_access"),
    path('register/', views.register_view, name='register'),
    path('access-pending/', views.access_pending_view, name='access_pending'),
    path('login/', views.login_view, name='login'),
    path('password/change/', views.password_change_view, name='password_change'),
    path('logout/', views.logout_view, name='logout'),
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
path('settings/', views.user_settings_view, name='settings'),
]
