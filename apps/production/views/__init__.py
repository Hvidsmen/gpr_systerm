"""
Реэкспорт всех views для обратной совместимости.
URLs импортируют views как: from apps.production import views
"""
from apps.production.views.daily_facts import (
    FactListView, FactCreateView, FactUpdateView, FactDeleteView,
)
from apps.production.views.labor_facts import (
    LaborFactListView, LaborFactCreateView, LaborFactUpdateView, LaborFactDeleteView,
    labor_fact_inline_update,
    LaborFactEditByDateView, LaborFactEditRangeView,
    LaborFactDeleteByDateView, LaborFactDeleteByBrigadeView,
)
from apps.production.views.equipment_facts import (
    EquipmentFactListView, EquipmentFactCreateView, EquipmentFactUpdateView, EquipmentFactDeleteView,
    equipment_fact_inline_update,
    EquipmentFactEditByDateView, EquipmentFactEditRangeView,
    EquipmentFactDeleteByDateView, EquipmentFactDeleteByTypeView,
)
from apps.production.views.fuel_facts import (
    FuelFactListView, FuelFactCreateView, FuelFactUpdateView, FuelFactDeleteView,
    fuel_fact_inline_update,
    FuelFactEditByDateView, FuelFactEditRangeView,
    FuelFactDeleteByDateView, FuelFactDeleteByTypeView,
)
from apps.production.views.labor_plans import (
    LaborPlanListView, LaborPlanCreateView, LaborPlanRangeCreateView,
    LaborPlanUpdateView, LaborPlanDeleteView,
    labor_plan_inline_update,
    LaborPlanEditByDateView, LaborPlanEditRangeView,
    LaborPlanDeleteByDateView, LaborPlanDeleteByBrigadeView,
)
from apps.production.views.equipment_plans import (
    EquipmentPlanListView, EquipmentPlanCreateView, EquipmentPlanRangeCreateView,
    EquipmentPlanUpdateView, EquipmentPlanDeleteView,
    equipment_plan_inline_update,
    EquipmentPlanEditByDateView, EquipmentPlanEditRangeView,
    EquipmentPlanDeleteByDateView, EquipmentPlanDeleteByTypeView,
)
from apps.production.views.fuel_plans import (
    FuelPlanListView, FuelPlanCreateView, FuelPlanRangeCreateView,
    FuelPlanUpdateView, FuelPlanDeleteView,
    fuel_plan_inline_update,
    FuelPlanEditByDateView, FuelPlanEditRangeView,
    FuelPlanDeleteByDateView, FuelPlanDeleteByTypeView,
)
from apps.production.views.master_screen import fact_daily_view
from apps.production.views.mass_input import LaborFactDailyInputView,EquipmentFactDailyInputView,FuelFactDailyInputView
from apps.production.views.fact_input import FactInputView
__all__ = [
    # Daily facts
    'FactListView', 'FactCreateView', 'FactUpdateView', 'FactDeleteView',
    # Labor facts
    'LaborFactListView', 'LaborFactCreateView', 'LaborFactUpdateView', 'LaborFactDeleteView',
    'labor_fact_inline_update',
    'LaborFactEditByDateView', 'LaborFactEditRangeView',
    'LaborFactDeleteByDateView', 'LaborFactDeleteByBrigadeView',
    # Equipment facts
    'EquipmentFactListView', 'EquipmentFactCreateView', 'EquipmentFactUpdateView', 'EquipmentFactDeleteView',
    'equipment_fact_inline_update',
    'EquipmentFactEditByDateView', 'EquipmentFactEditRangeView',
    'EquipmentFactDeleteByDateView', 'EquipmentFactDeleteByTypeView',
    # Fuel facts
    'FuelFactListView', 'FuelFactCreateView', 'FuelFactUpdateView', 'FuelFactDeleteView',
    'fuel_fact_inline_update',
    'FuelFactEditByDateView', 'FuelFactEditRangeView',
    'FuelFactDeleteByDateView', 'FuelFactDeleteByTypeView',
    # Labor plans
    'LaborPlanListView', 'LaborPlanCreateView', 'LaborPlanRangeCreateView',
    'LaborPlanUpdateView', 'LaborPlanDeleteView',
    'labor_plan_inline_update',
    'LaborPlanEditByDateView', 'LaborPlanEditRangeView',
    'LaborPlanDeleteByDateView', 'LaborPlanDeleteByBrigadeView',
    # Equipment plans
    'EquipmentPlanListView', 'EquipmentPlanCreateView', 'EquipmentPlanRangeCreateView',
    'EquipmentPlanUpdateView', 'EquipmentPlanDeleteView',
    'equipment_plan_inline_update',
    'EquipmentPlanEditByDateView', 'EquipmentPlanEditRangeView',
    'EquipmentPlanDeleteByDateView', 'EquipmentPlanDeleteByTypeView',
    # Fuel plans
    'FuelPlanListView', 'FuelPlanCreateView', 'FuelPlanRangeCreateView',
    'FuelPlanUpdateView', 'FuelPlanDeleteView',
    'fuel_plan_inline_update',
    'FuelPlanEditByDateView', 'FuelPlanEditRangeView',
    'FuelPlanDeleteByDateView', 'FuelPlanDeleteByTypeView',
    # Master screen & mass input
    'fact_daily_view', 'LaborFactDailyInputView', 'FactInputView'
]