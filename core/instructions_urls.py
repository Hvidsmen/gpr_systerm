from django.urls import path
from . import instructions_views as views
app_name='instructions'
urlpatterns=[path('',views.index,name='index'),path('<slug:slug>/',views.article,name='article')]
