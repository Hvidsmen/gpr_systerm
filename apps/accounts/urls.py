from django.urls import path
from . import views
from .access_groups import groups
from .directory_groups import group_search
from .directory_admin import add_from_ad
from . import impersonation_views
from .one_c import integration, table_structure
from .one_c_jobs import jobs
from .one_c_report import download_report
from .uat import discovery as uat_discovery, status as uat_status
from .portal_updates import updates, update_status
from .user_archive import bulk_archive
from . import invitation_views, training_views
from .fact_period_views import periods as fact_open_periods
from . import uat_sync_views
from .approval_views import settings_view as approval_settings
from .approval_action import action as approval_email_action

app_name = 'accounts'

urlpatterns = [
    path('approval/action/<str:token>/',approval_email_action,name='approval_email_action'),
    path('approval-settings/',approval_settings,name='approval_settings'),
    path('approval-settings/mail/',invitation_views.settings_view,name='approval_mail_settings'),
    path('admin/fact-periods/',fact_open_periods,name='fact_open_periods'),
    path('training/',training_views.groups,name='training_list'),
    path('training/health/',training_views.health,name='training_health'),
    path('training/consume/',training_views.consume,name='training_consume'),
    path('training/<uuid:pk>/',training_views.groups,name='training_edit'),
    path('training/<uuid:pk>/reset/',training_views.reset,name='training_reset'),
    path('training/<uuid:pk>/invite/',training_views.invite,name='training_invite'),
    path('training/<uuid:pk>/enter/',training_views.enter,name='training_enter'),
    path('users/invitations/',invitation_views.history,name='invitation_list'),
    path('users/invitations/prepare/',invitation_views.prepare,name='user_invite_bulk'),
    path('users/invitations/settings/',invitation_views.settings_view,name='invitation_settings'),
    path('users/invitations/<uuid:pk>/',invitation_views.detail,name='invitation_detail'),
    path('users/invitations/<uuid:pk>/status/',invitation_views.status,name='invitation_status'),
    path('users/<int:pk>/invite/',invitation_views.prepare,name='user_invite'),
    path('admin/update/',updates,name='portal_updates'),
    path('admin/update/status/',update_status,name='portal_update_status'),
    path('users/archive/', bulk_archive, name='user_bulk_archive'),
    path('integrations/1c/report/', download_report, name='one_c_report'),
    path('integrations/uat/', uat_discovery, name='uat'),
    path('integrations/uat/status/', uat_status, name='uat_status'),
    path('integrations/uat/waybills/', uat_sync_views.waybills, name='uat_waybills'),
    path('integrations/uat/repairs/', uat_sync_views.repairs, name='uat_repairs'),
    path('integrations/uat/vehicles/', uat_sync_views.vehicles, name='uat_vehicles'),
    path('integrations/uat/departments/', uat_sync_views.departments, name='uat_departments'),
    path('integrations/uat/journal/', uat_sync_views.journal, name='uat_journal'),
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
