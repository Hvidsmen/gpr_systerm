from django.urls import path
from . import views

app_name = 'production'

urlpatterns = [
    # Дневные факты (объёмы)
    path('', views.FactListView.as_view(), name='fact_list'),
    path('create/', views.FactCreateView.as_view(), name='fact_create'),
    path('facts/<int:pk>/update/', views.FactUpdateView.as_view(), name='fact_update'),
    path('facts/<int:pk>/delete/', views.FactDeleteView.as_view(), name='fact_delete'),

    # Факты по людям
    path('labor/', views.LaborFactListView.as_view(), name='labor_list'),
    path('labor/create/', views.LaborFactCreateView.as_view(), name='labor_create'),
    path('labor/<int:pk>/update/', views.LaborFactUpdateView.as_view(), name='labor_update'),
    path('labor/<int:pk>/delete/', views.LaborFactDeleteView.as_view(), name='labor_delete'),

    # Факты по людям
    path('labor-facts/', views.LaborFactListView.as_view(), name='labor_fact_list'),
    path('labor-facts/create/', views.LaborFactCreateView.as_view(), name='labor_fact_create'),
    path('labor-facts/<int:pk>/update/', views.LaborFactUpdateView.as_view(), name='labor_fact_update'),
    path('labor-facts/<int:pk>/delete/', views.LaborFactDeleteView.as_view(), name='labor_fact_delete'),
    path('labor-facts/inline-update/', views.labor_fact_inline_update, name='labor_fact_inline_update'),

# Массовый ввод фактов
# Массовый ввод фактов
path('labor-facts/daily-input/', views.LaborFactDailyInputView.as_view(), name='labor_fact_daily_input'),
path('equipment-facts/daily-input/', views.EquipmentFactDailyInputView.as_view(), name='equipment_fact_daily_input'),
path('fuel-facts/daily-input/', views.FuelFactDailyInputView.as_view(), name='fuel_fact_daily_input'),
#

    # Массовые операции с фактами по людям
    path('labor-facts/date/<str:date_str>/edit/', views.LaborFactEditByDateView.as_view(),
         name='labor_fact_edit_by_date'),
    path('labor-facts/date/<str:date_str>/delete/', views.LaborFactDeleteByDateView.as_view(),
         name='labor_fact_delete_by_date'),
    path('labor-facts/range/edit/', views.LaborFactEditRangeView.as_view(), name='labor_fact_edit_range'),
    path('labor-facts/brigade/<int:brigade_id>/delete/', views.LaborFactDeleteByBrigadeView.as_view(),
         name='labor_fact_delete_by_brigade'),

    # Факты по технике
    path('equipment/', views.EquipmentFactListView.as_view(), name='equipment_list'),
    path('equipment/create/', views.EquipmentFactCreateView.as_view(), name='equipment_create'),
    path('equipment/<int:pk>/update/', views.EquipmentFactUpdateView.as_view(), name='equipment_update'),
    path('equipment/<int:pk>/delete/', views.EquipmentFactDeleteView.as_view(), name='equipment_delete'),
    # Факты по технике
    path('equipment-facts/', views.EquipmentFactListView.as_view(), name='equipment_fact_list'),
    path('equipment-facts/create/', views.EquipmentFactCreateView.as_view(), name='equipment_fact_create'),
    path('equipment-facts/<int:pk>/update/', views.EquipmentFactUpdateView.as_view(), name='equipment_fact_update'),
    path('equipment-facts/<int:pk>/delete/', views.EquipmentFactDeleteView.as_view(), name='equipment_fact_delete'),
    path('equipment-facts/inline-update/', views.equipment_fact_inline_update, name='equipment_fact_inline_update'),

    # Массовые операции с фактами по технике
    path('equipment-facts/date/<str:date_str>/edit/', views.EquipmentFactEditByDateView.as_view(),
         name='equipment_fact_edit_by_date'),
    path('equipment-facts/date/<str:date_str>/delete/', views.EquipmentFactDeleteByDateView.as_view(),
         name='equipment_fact_delete_by_date'),
    path('equipment-facts/range/edit/', views.EquipmentFactEditRangeView.as_view(), name='equipment_fact_edit_range'),
    path('equipment-facts/type/<int:equipment_type_id>/delete/', views.EquipmentFactDeleteByTypeView.as_view(),
         name='equipment_fact_delete_by_type'),
    # Факты по ГСМ
    path('fuel/', views.FuelFactListView.as_view(), name='fuel_list'),
    path('fuel/create/', views.FuelFactCreateView.as_view(), name='fuel_create'),
    path('fuel/<int:pk>/update/', views.FuelFactUpdateView.as_view(), name='fuel_update'),
    path('fuel/<int:pk>/delete/', views.FuelFactDeleteView.as_view(), name='fuel_delete'),
    # Факты по ГСМ
    path('fuel-facts/', views.FuelFactListView.as_view(), name='fuel_fact_list'),
    path('fuel-facts/create/', views.FuelFactCreateView.as_view(), name='fuel_fact_create'),
    path('fuel-facts/<int:pk>/update/', views.FuelFactUpdateView.as_view(), name='fuel_fact_update'),
    path('fuel-facts/<int:pk>/delete/', views.FuelFactDeleteView.as_view(), name='fuel_fact_delete'),
    path('fuel-facts/inline-update/', views.fuel_fact_inline_update, name='fuel_fact_inline_update'),

    # Массовые операции с фактами по ГСМ
    path('fuel-facts/date/<str:date_str>/edit/', views.FuelFactEditByDateView.as_view(), name='fuel_fact_edit_by_date'),
    path('fuel-facts/date/<str:date_str>/delete/', views.FuelFactDeleteByDateView.as_view(),
         name='fuel_fact_delete_by_date'),
    path('fuel-facts/range/edit/', views.FuelFactEditRangeView.as_view(), name='fuel_fact_edit_range'),
    path('fuel-facts/type/<str:fuel_type>/delete/', views.FuelFactDeleteByTypeView.as_view(),
         name='fuel_fact_delete_by_type'),
    # Планы по людям
    path('labor-plans/', views.LaborPlanListView.as_view(), name='labor_plan_list'),
    path('labor-plans/create/', views.LaborPlanCreateView.as_view(), name='labor_plan_create'),
    path('labor-plans/create/range/', views.LaborPlanRangeCreateView.as_view(), name='labor_plan_create_range'),
    path('labor-plans/<int:pk>/update/', views.LaborPlanUpdateView.as_view(), name='labor_plan_update'),
    path('labor-plans/<int:pk>/delete/', views.LaborPlanDeleteView.as_view(), name='labor_plan_delete'),
    # Inline обновление плана по людям
    path('labor-plans/inline-update/', views.labor_plan_inline_update, name='labor_plan_inline_update'),
    # Массовые операции с планами по людям
    path('labor-plans/date/<str:date_str>/delete/', views.LaborPlanDeleteByDateView.as_view(),
         name='labor_plan_delete_by_date'),
    path('labor-plans/date/<str:date_str>/edit/', views.LaborPlanEditByDateView.as_view(),
         name='labor_plan_edit_by_date'),
    path('labor-plans/brigade/<int:brigade_id>/delete/', views.LaborPlanDeleteByBrigadeView.as_view(),
         name='labor_plan_delete_by_brigade'),
    # Массовое редактирование диапазона планов по людям
    path('labor-plans/range/edit/', views.LaborPlanEditRangeView.as_view(), name='labor_plan_edit_range'),

    # Планы по технике
    path('equipment-plans/', views.EquipmentPlanListView.as_view(), name='equipment_plan_list'),
    path('equipment-plans/create/', views.EquipmentPlanCreateView.as_view(), name='equipment_plan_create'),
    path('equipment-plans/create/range/', views.EquipmentPlanRangeCreateView.as_view(),
         name='equipment_plan_create_range'),
    path('equipment-plans/<int:pk>/update/', views.EquipmentPlanUpdateView.as_view(), name='equipment_plan_update'),
    path('equipment-plans/<int:pk>/delete/', views.EquipmentPlanDeleteView.as_view(), name='equipment_plan_delete'),

    # Планы по ГСМ
    path('fuel-plans/', views.FuelPlanListView.as_view(), name='fuel_plan_list'),
    path('fuel-plans/create/', views.FuelPlanCreateView.as_view(), name='fuel_plan_create'),
    path('fuel-plans/create/range/', views.FuelPlanRangeCreateView.as_view(), name='fuel_plan_create_range'),
    path('fuel-plans/<int:pk>/update/', views.FuelPlanUpdateView.as_view(), name='fuel_plan_update'),
    path('fuel-plans/<int:pk>/delete/', views.FuelPlanDeleteView.as_view(), name='fuel_plan_delete'),

    # Экран мастера
    path('daily/', views.fact_daily_view, name='fact_daily'),

    # Создание ресурсов для конкретной работы
    path('work/<int:work_pk>/labor/create/', views.LaborFactCreateView.as_view(), name='labor_create_for_work'),
    path('work/<int:work_pk>/equipment/create/', views.EquipmentFactCreateView.as_view(),
         name='equipment_create_for_work'),
    path('work/<int:work_pk>/fuel/create/', views.FuelFactCreateView.as_view(), name='fuel_create_for_work'),
    # Планы по технике
    path('equipment-plans/', views.EquipmentPlanListView.as_view(), name='equipment_plan_list'),
    path('equipment-plans/create/', views.EquipmentPlanCreateView.as_view(), name='equipment_plan_create'),
    path('equipment-plans/create/range/', views.EquipmentPlanRangeCreateView.as_view(),
         name='equipment_plan_create_range'),
    path('equipment-plans/<int:pk>/update/', views.EquipmentPlanUpdateView.as_view(), name='equipment_plan_update'),
    path('equipment-plans/<int:pk>/delete/', views.EquipmentPlanDeleteView.as_view(), name='equipment_plan_delete'),
    path('equipment-plans/inline-update/', views.equipment_plan_inline_update, name='equipment_plan_inline_update'),

    # Массовые операции по технике
    path('equipment-plans/date/<str:date_str>/edit/', views.EquipmentPlanEditByDateView.as_view(),
         name='equipment_plan_edit_by_date'),
    path('equipment-plans/date/<str:date_str>/delete/', views.EquipmentPlanDeleteByDateView.as_view(),
         name='equipment_plan_delete_by_date'),
    path('equipment-plans/range/edit/', views.EquipmentPlanEditRangeView.as_view(), name='equipment_plan_edit_range'),
    path('equipment-plans/type/<int:equipment_type_id>/delete/', views.EquipmentPlanDeleteByTypeView.as_view(),
         name='equipment_plan_delete_by_type'),

    # Планы по ГСМ
    path('fuel-plans/', views.FuelPlanListView.as_view(), name='fuel_plan_list'),
    path('fuel-plans/create/', views.FuelPlanCreateView.as_view(), name='fuel_plan_create'),
    path('fuel-plans/create/range/', views.FuelPlanRangeCreateView.as_view(), name='fuel_plan_create_range'),
    path('fuel-plans/<int:pk>/update/', views.FuelPlanUpdateView.as_view(), name='fuel_plan_update'),
    path('fuel-plans/<int:pk>/delete/', views.FuelPlanDeleteView.as_view(), name='fuel_plan_delete'),
    path('fuel-plans/inline-update/', views.fuel_plan_inline_update, name='fuel_plan_inline_update'),

    # Массовые операции по ГСМ
    path('fuel-plans/date/<str:date_str>/edit/', views.FuelPlanEditByDateView.as_view(), name='fuel_plan_edit_by_date'),
    path('fuel-plans/date/<str:date_str>/delete/', views.FuelPlanDeleteByDateView.as_view(),
         name='fuel_plan_delete_by_date'),
    path('fuel-plans/range/edit/', views.FuelPlanEditRangeView.as_view(), name='fuel_plan_edit_range'),
    path('fuel-plans/type/<str:fuel_type>/delete/', views.FuelPlanDeleteByTypeView.as_view(),
         name='fuel_plan_delete_by_type'),
# Ввод факта работ с фильтрацией
path('fact-input/', views.FactInputView.as_view(), name='fact_input'),
]
