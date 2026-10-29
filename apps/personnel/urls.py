from django.urls import path
from . import views, housing_views
app_name='personnel'
urlpatterns=[
    path('housing/',housing_views.workspace,name='housing'),
    path('housing/export/',housing_views.roster_export,name='housing_export'),
    path('camps/',housing_views.camps,name='camps'),
    path('camps/create/',housing_views.camp_edit,name='camp_create'),
    path('camps/<int:pk>/',housing_views.camp_edit,name='camp_edit'),
    path('housing/people/<int:pk>/',housing_views.history,name='housing_history'),
    path('housing/stays/<int:pk>/cancel/',housing_views.cancel,name='housing_cancel'),
    path('people/<int:pk>/individual-calendar/',housing_views.individual_calendar,name='individual_calendar'),
    path('people/<int:pk>/schedule/',housing_views.schedule,name='person_schedule'),
    path('people/link/',housing_views.link_people,name='link_people'),
    path('',views.board_list,name='board_list'),
    path('create/',views.board_create,name='board_create'),
    path('import/',views.import_excel,name='import'),
    path('people/',views.people,name='people'),
    path('people/create/',views.person_edit,name='person_create'),
    path('people/<int:pk>/',views.person_edit,name='person_edit'),
    path('<int:pk>/',views.board_detail,name='board_detail'),
    path('<int:pk>/export/',views.export,name='export'),
    path('<int:board_pk>/patterns/create/',views.pattern_create,name='pattern_create'),
    path('<int:board_pk>/groups/create/',views.group_edit,name='group_create'),
    path('<int:board_pk>/groups/<int:pk>/',views.group_edit,name='group_edit'),
    path('groups/<int:group_pk>/slots/create/',views.slot_edit,name='slot_create'),
    path('groups/<int:group_pk>/slots/<int:pk>/',views.slot_edit,name='slot_edit'),
    path('slots/<int:pk>/calendar/',views.calendar_edit,name='calendar_edit'),
]

from .housing_reconcile import reconcile
urlpatterns += [path('housing/reconcile/',reconcile,name='housing_reconcile')]

from . import lodging_views
urlpatterns += [
    path('lodging/',lodging_views.board,name='lodging'),
    path('lodging/employees/<int:pk>/gender/',lodging_views.employee_gender,name='lodging_employee_gender'),
    path('lodging/claims/',lodging_views.claims,name='lodging_claims'),
    path('lodging/available-beds/',lodging_views.available_beds,name='lodging_available_beds'),
    path('lodging/beds/quick-create/',lodging_views.quick_bed,name='lodging_quick_bed'),
    path('lodging/sync/',lodging_views.sync,name='lodging_sync'),
    path('lodging/sync/status/',lodging_views.sync_status,name='lodging_sync_status'),
    path('lodging/buildings/create/',lodging_views.building_edit,name='lodging_building_create'),
    path('lodging/buildings/<int:pk>/',lodging_views.building_edit,name='lodging_building_edit'),
    path('lodging/buildings/<int:building_pk>/beds/create/',lodging_views.bed_edit,name='lodging_bed_create'),
    path('lodging/buildings/<int:building_pk>/beds/<int:pk>/',lodging_views.bed_edit,name='lodging_bed_edit'),
    path('lodging/stays/<int:pk>/',lodging_views.stay_edit,name='lodging_stay_edit'),
]

from .initial_departure import edit as initial_departure
urlpatterns += [path("people/<int:pk>/initial-departure/",initial_departure,name="initial_departure")]

from . import lodging_import
urlpatterns += [path("lodging/import/",lodging_import.upload,name="lodging_import"),path("lodging/import/<int:pk>/",lodging_import.review,name="lodging_import_review")]
