from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    path("users/create/", views.CompanyUserCreateView.as_view(), name="user_create"),
    path("users/<int:pk>/access/", views.CompanyUserAccessView.as_view(), name="user_access"),
    path('register/', views.register_view, name='register'),
    path('access-pending/', views.access_pending_view, name='access_pending'),
    path('login/', views.login_view, name='login'),
    path('password/change/', views.password_change_view, name='password_change'),
    path('logout/', views.logout_view, name='logout'),
    path('users/', views.UserListView.as_view(), name='user_list'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),
path('settings/', views.user_settings_view, name='settings'),
]