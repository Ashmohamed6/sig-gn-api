# admin_core/urls.py
"""
URLs pour l'administration - Conventions REST standards
"""
from django.urls import path

from .views import (
    AdminUserListCreateView,
    AdminUserDetailView,
    AdminUserActivateView,
    AdminUserDeactivateView,
    AdminUserResetPasswordView,
    AdminUserStatsView,
    AdminDashboardView,
)

app_name = 'admin_core'

urlpatterns = [
    # Dashboard admin
    path('dashboard/', AdminDashboardView.as_view(), name='admin_dashboard'),
    
    # Gestion des utilisateurs
    # Liste et création (REST standard)
    path('users/', AdminUserListCreateView.as_view(), name='admin_users_list_create'),
    
    # Statistiques (avant :id pour éviter conflit)
    path('users/stats/', AdminUserStatsView.as_view(), name='admin_users_stats'),
    
    # Actions sur un utilisateur spécifique (REST standard)
    path('users/<int:id>/', AdminUserDetailView.as_view(), name='admin_users_detail'),
    
    # Actions spéciales
    path('users/<int:id>/activate/', AdminUserActivateView.as_view(), name='admin_users_activate'),
    path('users/<int:id>/deactivate/', AdminUserDeactivateView.as_view(), name='admin_users_deactivate'),
    path('users/<int:id>/reset-password/', AdminUserResetPasswordView.as_view(), name='admin_users_reset_password'),
]
