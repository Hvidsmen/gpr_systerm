from django.urls import path
from . import views

app_name = 'planning'

urlpatterns = [
    path('', views.PlanListView.as_view(), name='plan_list'),
    path('create/', views.MonthlyPlanCreateView.as_view(), name='plan_create'),
    path('<int:pk>/', views.PlanDetailView.as_view(), name='plan_detail'),
# Добавьте после path('<int:pk>/', ...)
path('<int:plan_pk>/versions/', views.PlanVersionsListView.as_view(), name='plan_versions'),
    path('versions/<int:pk>/', views.PlanVersionDetailView.as_view(), name='version_detail'),
    path('versions/<int:pk>/generate/', views.VersionGenerateView.as_view(), name='version_generate'),
    path('versions/<int:pk>/submit/', views.VersionSubmitView.as_view(), name='version_submit'),
    path('versions/<int:pk>/approve/', views.VersionApproveView.as_view(), name='version_approve'),
    path('versions/<int:pk>/complete/', views.VersionCompleteView.as_view(), name='version_complete'),
    path('versions/<int:pk>/reject/', views.VersionRejectView.as_view(), name='version_reject'),
    path('versions/<int:pk>/revision/', views.VersionRevisionView.as_view(), name='version_revision'),
    path('<int:pk>/update/', views.MonthlyPlanUpdateView.as_view(), name='plan_update'),  # НОВОЕ
    path('<int:pk>/delete/', views.MonthlyPlanDeleteView.as_view(), name='plan_delete'),
    path('plans/<int:plan_pk>/versions/create/', views.VersionCreateView.as_view(), name='version_create'),
path('versions/<int:pk>/regenerate/', views.VersionRegenerateView.as_view(), name='version_regenerate'),
    # Профили нагрузки
    path('profiles/', views.LoadProfileListView.as_view(), name='profile_list'),
    path('profiles/create/', views.LoadProfileCreateView.as_view(), name='profile_create'),
    path('profiles/<int:pk>/', views.LoadProfileDetailView.as_view(), name='profile_detail'),  # НОВОЕ
    path('profiles/<int:pk>/update/', views.LoadProfileUpdateView.as_view(), name='profile_update'),  # НОВОЕ
    path('profiles/<int:pk>/delete/', views.LoadProfileDeleteView.as_view(), name='profile_delete'),  # НОВОЕ
    path('profiles/<int:profile_pk>/items/create/', views.LoadProfileItemCreateView.as_view(),
         name='profile_item_create'),  # НОВОЕ
    path('profile-items/<int:pk>/update/', views.LoadProfileItemUpdateView.as_view(), name='profile_item_update'),
    # НОВОЕ
    path('profile-items/<int:pk>/delete/', views.LoadProfileItemDeleteView.as_view(), name='profile_item_delete'),
    # НОВОЕ
# Inline обновление дневного плана
path('daily-plans/inline-update/', views.daily_plan_inline_update, name='daily_plan_inline_update'),
# Установка актуальной версии
path('versions/<int:pk>/set-baseline/', views.SetBaselineVersionView.as_view(), name='set_baseline_version'),
    # Календари

 path('calendars/', views.CalendarListView.as_view(), name='calendar_list'),
    path('calendars/create/', views.CalendarCreateView.as_view(), name='calendar_create'),
    path('calendars/<int:pk>/', views.CalendarDetailView.as_view(), name='calendar_detail'),
    path('calendars/<int:pk>/update/', views.CalendarUpdateView.as_view(), name='calendar_update'),
    path('calendars/<int:pk>/delete/', views.CalendarDeleteView.as_view(), name='calendar_delete'),
    path('calendars/<int:pk>/autofill/', views.CalendarAutoFillView.as_view(), name='calendar_autofill'),
    path('calendar-days/<int:pk>/update/', views.CalendarDayUpdateView.as_view(), name='calendar_day_update'),
    path('calendars/<int:pk>/bulk-edit/', views.CalendarDayBulkEditView.as_view(), name='calendar_bulk_edit'),
# Матрица дневных планов по иерархии
path('matrix/', views.PlanMatrixView.as_view(), name='plan_matrix'),
]

from .global_views import GlobalList, GlobalCreate, GlobalDetail, GlobalAction
urlpatterns += [
    path('global/', GlobalList.as_view(), name='global_list'),
    path('global/create/', GlobalCreate.as_view(), name='global_create'),
    path('global/<int:pk>/', GlobalDetail.as_view(), name='global_detail'),
    path('global/<int:pk>/<str:action>/', GlobalAction.as_view(), name='global_action'),
]

from .workspace_views import WorkspaceList, WorkspaceCreate, WorkspaceDetail, ForecastCreate, WorkspaceEdit, AddWorkspaceWork, WorkspaceWorkCreate
urlpatterns += [
    path('workspace/', WorkspaceList.as_view(), name='workspace_list'),
    path('workspace/create/', WorkspaceCreate.as_view(), name='workspace_create'),
    path('workspace/<int:pk>/', WorkspaceDetail.as_view(), name='workspace_detail'),
    path('workspace/<int:pk>/forecast/', ForecastCreate.as_view(), name='workspace_forecast'),
    path('workspace/<int:pk>/works/create/', WorkspaceWorkCreate.as_view(), name='workspace_work_create'),
    path('workspace-versions/<int:pk>/edit/', WorkspaceEdit.as_view(), name='workspace_edit'),
    path('workspace-versions/<int:pk>/add-work/', AddWorkspaceWork.as_view(), name='workspace_add_work'),
]
from .workspace_views import WorkspaceExport
urlpatterns += [path('workspace-versions/<int:pk>/export/',WorkspaceExport.as_view(),name='workspace_export')]
