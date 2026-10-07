from django.urls import path
from . import views
from .price_views import WorkPriceChange
from .unit_views import UnitUpdate, UnitDelete
from .catalogs import WorkGroupList, MeasurementUnitList, quick_create

app_name = 'works'

urlpatterns = [
    path("<int:pk>/price/", WorkPriceChange.as_view(), name="price_change"),
    path("catalogs/groups/", WorkGroupList.as_view(), name="group_list"),
    path("catalogs/groups/create/", quick_create("group"), name="group_create"),
    path("catalogs/units/", MeasurementUnitList.as_view(), name="unit_list"),
    path("catalogs/units/create/", quick_create("unit"), name="unit_create"),
    path("catalogs/units/<int:pk>/update/", UnitUpdate.as_view(), name="unit_update"),
    path("catalogs/units/<int:pk>/delete/", UnitDelete.as_view(), name="unit_delete"),
    # Список всех работ
    path('', views.WorkListView.as_view(), name='work_list'),

    # Создание НОВОЙ РАБОТЫ (не подработы!)
    path('create/', views.WorkCreateView.as_view(), name='work_create'),

    # Детали работы (список подработ внутри)
    path('<int:pk>/', views.WorkDetailView.as_view(), name='work_detail'),

    # Редактирование работы
    path('<int:pk>/update/', views.WorkUpdateView.as_view(), name='work_update'),

    # Удаление работы
    path('<int:pk>/delete/', views.WorkDeleteView.as_view(), name='work_delete'),

    # === ПОДРАБОТЫ (внутри работы) ===

    # Создание подработы для конкретной работы
    path('<int:work_pk>/item/create/', views.WorkItemCreateView.as_view(), name='work_item_create'),

    # Редактирование подработы
    path('item/<int:pk>/update/', views.WorkItemUpdateView.as_view(), name='work_item_update'),

    # Удаление подработы
    path('item/<int:pk>/delete/', views.WorkItemDeleteView.as_view(), name='work_item_delete'),
# apps/works/urls.py

path('section/create/', views.WorkSectionCreateView.as_view(), name='section_create'),
]
from core.bulk_delete import BulkDelete
urlpatterns += [path("bulk-delete/<str:kind>/", BulkDelete.as_view(allowed_kinds=('works', 'work_groups', 'units')), name="bulk_delete")]

from .merge_views import WorkMergeView
urlpatterns += [path("merge/", WorkMergeView.as_view(), name="work_merge")]

from .batch_prepare import WorkBatchPrepareView
urlpatterns += [path("batch-prepare/", WorkBatchPrepareView.as_view(), name="batch_prepare")]
