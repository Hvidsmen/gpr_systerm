from django.urls import path
from . import views
app_name='personnel'
urlpatterns=[
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
