from django.urls import path
from . import views

from .equipment_import import EquipmentTypeImport, equipment_import_template

app_name = 'resources'

urlpatterns = [
    path('equipment-types/import/', EquipmentTypeImport.as_view(), name='equipment_type_import'),
    path('equipment-types/import/template/', equipment_import_template, name='equipment_import_template'),
    path('equipment-categories/', views.EquipmentCategoryListView.as_view(), name='equipment_category_list'),
    path('equipment-categories/create/', views.equipment_category_create, name='equipment_category_create'),
    path('employees/', views.EmployeeListView.as_view(), name='employee_list'),
    path('employees/create/', views.EmployeeCreateView.as_view(), name='employee_create'),
    path('brigades/', views.BrigadeListView.as_view(), name='brigade_list'),
    path('brigades/create/', views.BrigadeCreateView.as_view(), name='brigade_create'),
    path('brigades/<int:pk>/update/', views.BrigadeUpdateView.as_view(), name='brigade_update'),
    path('brigades/<int:pk>/delete/', views.BrigadeDeleteView.as_view(), name='brigade_delete'),
# Справочник видов техники
path('equipment-types/', views.EquipmentTypeListView.as_view(), name='equipment_type_list'),
path('equipment-types/create/', views.EquipmentTypeCreateView.as_view(), name='equipment_type_create'),
path('equipment-types/<int:pk>/update/', views.EquipmentTypeUpdateView.as_view(), name='equipment_type_update'),
path('equipment-types/<int:pk>/delete/', views.EquipmentTypeDeleteView.as_view(), name='equipment_type_delete'),
]
from .brigade_catalogs import BrigadeCatalogList, quick_create
urlpatterns += [
    path('brigade-groups/', BrigadeCatalogList.as_view(kind='group'), name='brigade_group_list'),
    path('brigade-groups/create/', quick_create('group'), name='brigade_group_create'),
    path('brigade-macro-groups/', BrigadeCatalogList.as_view(kind='macro'), name='brigade_macro_group_list'),
    path('brigade-macro-groups/create/', quick_create('macro'), name='brigade_macro_group_create'),
]
