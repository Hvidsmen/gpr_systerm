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

from .global_views import GlobalList, GlobalCreate, GlobalDetail, GlobalAction, ApprovalList
urlpatterns += [
    path('approvals/', ApprovalList.as_view(), name='approval_list'),
    path('global/', GlobalList.as_view(), name='global_list'),
    path('global/create/', GlobalCreate.as_view(), name='global_create'),
    path('global/<int:pk>/', GlobalDetail.as_view(), name='global_detail'),
]

from .workspace_views import WorkspaceList, WorkspaceCreate, WorkspaceDetail, ForecastCreate, WorkspaceEdit, AddWorkspaceWork, AddWorkspaceResource, WorkspaceWorkCreate
urlpatterns += [
    path('workspace/', WorkspaceList.as_view(), name='workspace_list'),
    path('workspace/create/', WorkspaceCreate.as_view(), name='workspace_create'),
    path('workspace/<int:pk>/', WorkspaceDetail.as_view(), name='workspace_detail'),
    path('workspace/<int:pk>/forecast/', ForecastCreate.as_view(), name='workspace_forecast'),
    path('workspace/<int:pk>/works/create/', WorkspaceWorkCreate.as_view(), name='workspace_work_create'),
    path('workspace-versions/<int:pk>/edit/', WorkspaceEdit.as_view(), name='workspace_edit'),
    path('workspace-versions/<int:pk>/add-resource/<str:kind>/', AddWorkspaceResource.as_view(), name='workspace_add_resource'),
    path('workspace-versions/<int:pk>/add-work/', AddWorkspaceWork.as_view(), name='workspace_add_work'),
]
from .workspace_views import WorkspaceExport
urlpatterns += [path('workspace-versions/<int:pk>/export/',WorkspaceExport.as_view(),name='workspace_export')]

from .deletion import WorkspaceDelete, GlobalDelete
urlpatterns = [
    path('workspace/<int:pk>/delete/', WorkspaceDelete.as_view(), name='workspace_delete'),
    path('global/<int:pk>/delete/', GlobalDelete.as_view(), name='global_delete'),
] + urlpatterns

from .bulk_add import WorkspaceBulkAdd
urlpatterns += [path('workspace-versions/<int:pk>/add-list/<str:kind>/', WorkspaceBulkAdd.as_view(), name='workspace_bulk_add')]

from .workspace_excel import WorkspaceExcelExport, WorkspaceExcelImport
urlpatterns += [
    path('workspace-versions/<int:pk>/excel/', WorkspaceExcelExport.as_view(), name='workspace_excel_export'),
    path('workspace-versions/<int:pk>/excel/import/', WorkspaceExcelImport.as_view(), name='workspace_excel_import'),
]


from .project_plan_views import ProjectPlanList, ProjectPlanCreate, ProjectPlanUpdate, ProjectPlanDetail, AssignProjectPlan
urlpatterns += [
    path('project-plans/', ProjectPlanList.as_view(), name='project_plan_list'),
    path('project-plans/create/', ProjectPlanCreate.as_view(), name='project_plan_create'),
    path('project-plans/<int:pk>/', ProjectPlanDetail.as_view(), name='project_plan_detail'),
    path('project-plans/<int:pk>/update/', ProjectPlanUpdate.as_view(), name='project_plan_update'),
    path('workspace/<int:pk>/project-plan/', AssignProjectPlan.as_view(), {'workspace_mode': True}, name='workspace_project_assign'),
    path('global/<int:pk>/project-plan/assign/', AssignProjectPlan.as_view(), name='global_project_assign'),
]

from .report_views import MatrixReport
urlpatterns += [path('reports/plan-fact/', MatrixReport.as_view(), name='report_matrix')]

from .meeting_import import MeetingImportView
urlpatterns += [path("meeting-import/", MeetingImportView.as_view(), name="meeting_import")]

urlpatterns += [path("workspace-versions/<int:pk>/meeting-import/", MeetingImportView.as_view(), name="workspace_meeting_import")]

from core.bulk_delete import BulkDelete
urlpatterns += [path("bulk-delete/<str:kind>/", BulkDelete.as_view(allowed_kinds=('profiles', 'calendars')), name="bulk_delete")]

from apps.production.journal_delete import JournalBulkDelete
urlpatterns += [path('journals/<str:kind>/delete/', JournalBulkDelete.as_view(
    allowed_kinds=('monthly_plans',)), name='journal_bulk_delete')]

from .report_views import PlanJournal, FactJournal
urlpatterns += [path('journals/plans/', PlanJournal.as_view(), name='journal_plan_list'), path('journals/facts/', FactJournal.as_view(), name='journal_fact_list')]

from .hierarchy_views import DistributionDetail, SmgCreate
urlpatterns += [path('gpr/<int:pk>/distribution/', DistributionDetail.as_view(), name='gpr_distribution_detail'), path('gpr/<int:pk>/smg/create/', SmgCreate.as_view(), name='smg_create')]

from .hierarchy_views import SmgDaysEdit
urlpatterns += [path('smg/<int:pk>/days/edit/', SmgDaysEdit.as_view(), name='smg_days_edit')]

from .gpr_excel_views import GprExcelImport
urlpatterns += [path('gpr/excel-import/', GprExcelImport.as_view(), name='gpr_excel_import')]

urlpatterns += [path('workspace-versions/<int:pk>/gpr-import/', GprExcelImport.as_view(), name='workspace_gpr_import')]

from .carryover_views import CarryoverView
urlpatterns += [path('workspace-versions/<int:pk>/carryover/', CarryoverView.as_view(), name='workspace_carryover')]

from .workspace_views import AddRemainingWork
urlpatterns += [path('workspace-versions/<int:pk>/remaining-work/', AddRemainingWork.as_view(), name='workspace_remaining_work')]

from .daily_schedule_views import DailyScheduleCreate, DailyScheduleDetail, DailyScheduleEdit, DailyScheduleApply
urlpatterns += [
    path('global/<int:pk>/daily-schedule/',DailyScheduleCreate.as_view(),name='daily_schedule_create'),
    path('daily-schedule/<int:pk>/',DailyScheduleDetail.as_view(),name='daily_schedule_detail'),
    path('daily-schedule/entry/<int:pk>/',DailyScheduleEdit.as_view(),name='daily_schedule_edit'),
    path('daily-schedule/<int:pk>/apply/',DailyScheduleApply.as_view(),name='daily_schedule_apply'),
]

from .carryover_task_views import CarryoverTaskList,CarryoverTaskDetail
urlpatterns += [
    path('carryover-tasks/',CarryoverTaskList.as_view(),name='carryover_task_list'),
    path('carryover-tasks/<int:pk>/',CarryoverTaskDetail.as_view(),name='carryover_task_detail'),
]

from .estimate_review_views import ReviewList, ReviewAssign, ReviewDetail
urlpatterns += [
    path('estimate-reviews/', ReviewList.as_view(), name='estimate_review_list'),
    path('estimate-reviews/assign/<str:kind>/<int:pk>/', ReviewAssign.as_view(), name='estimate_review_assign'),
    path('estimate-reviews/<int:pk>/', ReviewDetail.as_view(), name='estimate_review_detail'),
]

from .continuation_views import ContinueGpr
urlpatterns += [path('workspace/continue/',ContinueGpr.as_view(),name='workspace_continue')]

from .macro_history_views import MacroCreate,MacroDetail
urlpatterns += [path('macro/create/',MacroCreate.as_view(),name='macro_history_create'),path('macro/<int:pk>/',MacroDetail.as_view(),name='macro_history_detail')]

from .task_views import TaskList,TaskDetail,TaskCreate
urlpatterns += [path('tasks/',TaskList.as_view(),name='task_list'),path('tasks/create/',TaskCreate.as_view(),name='task_create'),path('tasks/<int:pk>/',TaskDetail.as_view(),name='task_detail')]

# Specific global routes must precede the generic POST action endpoint.
urlpatterns += [path('global/<int:pk>/<str:action>/', GlobalAction.as_view(), name='global_action')]

from .archive import ArchiveList, WorkspaceArchive
urlpatterns += [path("workspace/archive/", ArchiveList.as_view(), name="workspace_archive_list"), path("workspace/<int:pk>/archive/", WorkspaceArchive.as_view(), name="workspace_archive")]

from .independent_smg_views import SMGList, SMGCreate, SMGReference, SMGRevise
urlpatterns += [
    path('daily-schedule/', SMGList.as_view(), name='daily_schedule_list'),
    path('daily-schedule/create/', SMGCreate.as_view(), name='smg_create'),
    path('daily-schedule/<int:pk>/reference/', SMGReference.as_view(), name='smg_reference'),
    path('daily-schedule/<int:pk>/revision/', SMGRevise.as_view(), name='smg_revise'),
]

from .work_resource_views import WorkResources
urlpatterns += [
    path('workspace-versions/<int:pk>/resources/',WorkResources.as_view(kind='gpr'),name='work_resources_gpr'),
    path('workspace-versions/<int:pk>/resources/<int:assignment_pk>/',WorkResources.as_view(kind='gpr'),name='work_resource_edit_gpr'),
    path('daily-schedule/<int:pk>/resources/',WorkResources.as_view(kind='smg'),name='work_resources_smg'),
    path('daily-schedule/<int:pk>/resources/<int:assignment_pk>/',WorkResources.as_view(kind='smg'),name='work_resource_edit_smg'),
]

from .decomposition_report import DecompositionReport
urlpatterns += [
    path('decomposition/',DecompositionReport.as_view(),name='decomposition_report'),
    path('workspace-versions/<int:pk>/decomposition/',DecompositionReport.as_view(kind='gpr'),name='decomposition_gpr'),
    path('daily-schedule/<int:pk>/decomposition/',DecompositionReport.as_view(kind='smg'),name='decomposition_smg'),
]

from .design_preview import DesignPreview
urlpatterns += [path('design-preview/',DesignPreview.as_view(),name='design_preview')]

from .pivot_report import PivotReport
urlpatterns += [
    path('reports/pivot/', PivotReport.as_view(), name='pivot_report_list'),
    path('reports/pivot/facts/', PivotReport.as_view(source_kind='facts'), name='pivot_fact_list'),
    path('reports/pivot/gpr/<int:pk>/', PivotReport.as_view(source_kind='gpr'), name='pivot_gpr_detail'),
    path('reports/pivot/daily-schedule/<int:pk>/', PivotReport.as_view(source_kind='smg'), name='pivot_smg_detail'),
]

from .preparation_views import PreparationView
urlpatterns += [path('workspace-versions/<int:pk>/preparation/',PreparationView.as_view(kind='gpr'),name='preparation_gpr'),path('daily-schedule/<int:pk>/preparation/',PreparationView.as_view(kind='smg'),name='preparation_smg')]

from .work_selection_views import add_branch
urlpatterns += [path('workspace-versions/<int:pk>/add-branch/',add_branch,name='workspace_add_branch')]

from .task_admin import force_close
urlpatterns += [path('tasks/force-close/',force_close,name='task_force_close')]

from .resource_editor import add_assignment
urlpatterns += [path('workspace-versions/<int:pk>/resource-assignment/',add_assignment,name='preparation_add_assignment')]

from .object_plans import ObjectPlanList,ObjectPlanCreate,ObjectPlanDetail
urlpatterns += [
    path('object-plans/',ObjectPlanList.as_view(),name='object_plan_list'),
    path('object-plans/create/',ObjectPlanCreate.as_view(),name='object_plan_create'),
    path('object-plans/<int:pk>/',ObjectPlanDetail.as_view(),name='object_plan_detail'),
]

from .smg_copy import copy_view
urlpatterns += [path('daily-schedule/<int:pk>/copy-from-gpr/',copy_view,name='smg_copy_gpr')]

from . import resource_graph, graph_crew
urlpatterns += [
    path('workspace-versions/<int:pk>/resource-graph/',resource_graph.graph,name='work_resources_graph_gpr'),
    path('workspace-versions/<int:pk>/resource-links/',resource_graph.links,name='work_resources_links_gpr'),
    path('workspace-versions/<int:pk>/resource-crew/',graph_crew.crew,name='work_resources_crew_gpr'),
    path('daily-schedule/<int:pk>/resource-graph/',resource_graph.graph,{'kind':'smg'},name='work_resources_graph_smg'),
    path('daily-schedule/<int:pk>/resource-links/',resource_graph.links,{'kind':'smg'},name='work_resources_links_smg'),
    path('daily-schedule/<int:pk>/resource-crew/',graph_crew.crew,{'kind':'smg'},name='work_resources_crew_smg'),
    path('daily-schedule/<int:pk>/resource-assignment/',add_assignment,{'kind':'smg'},name='work_resources_save_smg'),
]
