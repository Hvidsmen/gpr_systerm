from django.urls import path
from . import views
from .purge import single_purge
from .employee_import import employee_zup_import
from .employee_cards import EmployeeDetail, EmployeeUpdate

from .equipment_import import EquipmentTypeImport, equipment_import_template

app_name = 'resources'

urlpatterns = [
    path('equipment-types/import/', EquipmentTypeImport.as_view(), name='equipment_type_import'),
    path('equipment-types/import/template/', equipment_import_template, name='equipment_import_template'),
    path('equipment-categories/', views.EquipmentCategoryListView.as_view(), name='equipment_category_list'),
    path('equipment-categories/create/', views.equipment_category_create, name='equipment_category_create'),
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('employees/import-zup/', employee_zup_import, name='employee_zup_import'),
    path('employees/create/', views.EmployeeCreateView.as_view(), name='employee_create'),
    path('employees/<int:pk>/', EmployeeDetail.as_view(), name='employee_detail'),
    path('employees/<int:pk>/update/', EmployeeUpdate.as_view(), name='employee_update'),
    path('brigades/', views.BrigadeListView.as_view(), name='brigade_list'),
    path('brigades/create/', views.BrigadeCreateView.as_view(), name='brigade_create'),
    path('brigades/<int:pk>/update/', views.BrigadeUpdateView.as_view(), name='brigade_update'),
    path('brigades/<int:pk>/delete/', single_purge, {'kind':'brigades'}, name='brigade_delete'),
# Справочник видов техники
path('equipment-types/', views.EquipmentTypeListView.as_view(), name='equipment_type_list'),
path('equipment-types/create/', views.EquipmentTypeCreateView.as_view(), name='equipment_type_create'),
path('equipment-types/<int:pk>/update/', views.EquipmentTypeUpdateView.as_view(), name='equipment_type_update'),
path('equipment-types/<int:pk>/delete/', single_purge, {'kind':'equipment'}, name='equipment_type_delete'),
]
from .brigade_catalogs import BrigadeCatalogList, quick_create
urlpatterns += [
    path('brigade-groups/', BrigadeCatalogList.as_view(kind='group'), name='brigade_group_list'),
    path('brigade-groups/create/', quick_create('group'), name='brigade_group_create'),
    path('brigade-macro-groups/', BrigadeCatalogList.as_view(kind='macro'), name='brigade_macro_group_list'),
    path('brigade-macro-groups/create/', quick_create('macro'), name='brigade_macro_group_create'),
]

from .brigade_import import BrigadeImport, brigade_import_template
urlpatterns += [
    path('brigades/import/', BrigadeImport.as_view(), name='brigade_import'),
    path('brigades/import/template/', brigade_import_template, name='brigade_import_template'),
]

from .catalog_export import BrigadeExport, EquipmentTypeExport
urlpatterns += [
    path('brigades/export/', BrigadeExport.as_view(), name='brigade_export'),
    path('equipment-types/export/', EquipmentTypeExport.as_view(), name='equipment_type_export'),
]

from core.bulk_delete import BulkDelete
urlpatterns += [path("bulk-delete/<str:kind>/", BulkDelete.as_view(allowed_kinds=('brigades', 'brigade_groups', 'brigade_macros', 'equipment', 'equipment_categories', 'employees')), name="bulk_delete")]

from .equipment_merge import EquipmentMergeView
urlpatterns += [path('equipment-types/merge/', EquipmentMergeView.as_view(), name='equipment_type_merge')]

from .brigade_merge import BrigadeMergeView
urlpatterns += [path('brigades/merge/', BrigadeMergeView.as_view(), name='brigade_merge')]

from .brigade_group_merge import BrigadeGroupMerge
urlpatterns += [path('brigade-groups/merge/', BrigadeGroupMerge.as_view(), name='brigade_group_merge')]

from .brigade_catalogs import BrigadeGroupUpdate
urlpatterns += [path('brigade-groups/<int:pk>/update/', BrigadeGroupUpdate.as_view(), name='brigade_group_update')]

from .equipment_category_manage import EquipmentCategoryMerge, EquipmentCategoryUpdate
urlpatterns += [
    path('equipment-categories/merge/', EquipmentCategoryMerge.as_view(), name='equipment_category_merge'),
    path('equipment-categories/<int:pk>/update/', EquipmentCategoryUpdate.as_view(), name='equipment_category_update'),
]

from .crew_views import EquipmentCrew, PositionCatalogue
urlpatterns += [
    path('positions/',PositionCatalogue.as_view(),name='position_list'),
    path('positions/<int:pk>/update/',PositionCatalogue.as_view(),name='position_update'),
    path('equipment-types/<int:pk>/crew/',EquipmentCrew.as_view(),name='equipment_crew'),
]

from .equipment_units import units, unit_detail
urlpatterns += [
    path('equipment-units/',units,name='equipment_unit_list'),
    path('equipment-units/create/',units,{'create':True},name='equipment_unit_create'),
    path('equipment-units/<int:pk>/card/',unit_detail,name='equipment_unit_detail'),
    path('equipment-units/<int:pk>/',units,name='equipment_unit_update'),
]

from .purge import purge
urlpatterns += [path('purge/<str:kind>/',purge,name='catalogue_purge')]

from .fleet_import import fleet_import
urlpatterns += [path('equipment/fleet-import/',fleet_import,name='fleet_import')]

from .employee_cards import employee_position_report
urlpatterns += [path('employees/<int:pk>/position-check/',employee_position_report,name='employee_position_report')]

from .zup_positions import from_employees
urlpatterns += [path('brigades/from-zup-employees/',from_employees,name='brigade_zup_employees')]

from .brigade_batch_merge import batch_merge
from .zup_positions import link_employees
urlpatterns += [path('brigades/merge-first/',batch_merge,name='brigade_batch_merge'),
                path('brigades/link-employees/',link_employees,name='brigade_link_employees')]

from .brigade_bulk_group import assign_groups
urlpatterns += [path('brigades/assign-groups/', assign_groups, name='brigade_bulk_group')]

from . import transport_import_views
urlpatterns += [
path('equipment/summary-import/',transport_import_views.upload,name='transport_import'),
path('equipment/summary-import/<int:pk>/',transport_import_views.review,name='transport_import_review'),
path('equipment/import-errors/',transport_import_views.errors,name='transport_import_errors'),
path('equipment/import-errors/<int:pk>/',transport_import_views.correction,name='transport_import_correction'),
]
