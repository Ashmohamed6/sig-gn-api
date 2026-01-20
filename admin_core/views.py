# admin_core/views.py
"""
Vues pour l'administration de la plateforme SIG Guinée
REST API standard
"""
from django.contrib.auth import get_user_model
from django.db.models import Q, Count
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import (
    AdminUserListSerializer,
    AdminUserDetailSerializer,
    AdminUserCreateSerializer,
    AdminUserUpdateSerializer,
)

UserModel = get_user_model()


class IsAdminPermission(permissions.BasePermission):
    """
    Permission personnalisée : seuls les administrateurs
    """
    def has_permission(self, request, view):
        return (
            request.user and 
            request.user.is_authenticated and 
            (request.user.is_superuser or getattr(request.user, 'role', '') == 'admin')
        )


class AdminUserListCreateView(generics.ListCreateAPIView):
    """
    GET  /api/admin/users/ - Liste tous les utilisateurs
    POST /api/admin/users/ - Crée un nouvel utilisateur
    """
    permission_classes = [IsAdminPermission]
    
    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AdminUserCreateSerializer
        return AdminUserListSerializer
    
    def get_queryset(self):
        queryset = UserModel.objects.all().select_related().prefetch_related('projects')
        
        # Recherche
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(username__icontains=search) |
                Q(email__icontains=search) |
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search)
            )
        
        # Filtre par rôle
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        
        # Filtre par statut actif
        is_active = self.request.query_params.get('is_active')
        if is_active in ['true', 'false']:
            queryset = queryset.filter(is_active=(is_active == 'true'))
        
        # Tri
        ordering = self.request.query_params.get('ordering', '-date_joined')
        if ordering:
            queryset = queryset.order_by(ordering)
        
        return queryset


class AdminUserDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    /api/admin/users/:id/ - Récupère un utilisateur
    PUT    /api/admin/users/:id/ - Met à jour (complet)
    PATCH  /api/admin/users/:id/ - Met à jour (partiel)
    DELETE /api/admin/users/:id/ - Supprime
    """
    permission_classes = [IsAdminPermission]
    queryset = UserModel.objects.all()
    lookup_field = 'id'
    
    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return AdminUserUpdateSerializer
        return AdminUserDetailSerializer
    
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        
        # Protection : impossible de supprimer son propre compte
        if instance.id == request.user.id:
            return Response(
                {"detail": "Vous ne pouvez pas supprimer votre propre compte."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Protection : impossible de supprimer le dernier admin
        if instance.is_superuser or getattr(instance, 'role', '') == 'admin':
            admin_count = UserModel.objects.filter(
                Q(is_superuser=True) | Q(role='admin')
            ).count()
            if admin_count <= 1:
                return Response(
                    {"detail": "Impossible de supprimer le dernier administrateur du système."},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        self.perform_destroy(instance)
        return Response(
            {"detail": "Utilisateur supprimé avec succès."},
            status=status.HTTP_200_OK
        )


class AdminUserActivateView(APIView):
    """
    POST /api/admin/users/:id/activate/
    Active un utilisateur
    """
    permission_classes = [IsAdminPermission]
    
    def post(self, request, id):
        try:
            user = UserModel.objects.get(id=id)
        except UserModel.DoesNotExist:
            return Response(
                {"detail": "Utilisateur non trouvé."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        if user.is_active:
            return Response(
                {"detail": "L'utilisateur est déjà actif."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user.is_active = True
        user.save(update_fields=['is_active'])
        
        serializer = AdminUserDetailSerializer(user)
        return Response(serializer.data)


class AdminUserDeactivateView(APIView):
    """
    POST /api/admin/users/:id/deactivate/
    Désactive un utilisateur
    """
    permission_classes = [IsAdminPermission]
    
    def post(self, request, id):
        try:
            user = UserModel.objects.get(id=id)
        except UserModel.DoesNotExist:
            return Response(
                {"detail": "Utilisateur non trouvé."},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Protection : impossible de désactiver son propre compte
        if user.id == request.user.id:
            return Response(
                {"detail": "Vous ne pouvez pas désactiver votre propre compte."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not user.is_active:
            return Response(
                {"detail": "L'utilisateur est déjà inactif."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        user.is_active = False
        user.save(update_fields=['is_active'])
        
        serializer = AdminUserDetailSerializer(user)
        return Response(serializer.data)


class AdminUserStatsView(APIView):
    """
    GET /api/admin/users/stats/
    Statistiques sur les utilisateurs
    """
    permission_classes = [IsAdminPermission]
    
    def get(self, request):
        total_users = UserModel.objects.count()
        active_users = UserModel.objects.filter(is_active=True).count()
        inactive_users = total_users - active_users
        
        # Statistiques par rôle
        stats_by_role = {}
        roles = UserModel.objects.values_list('role', flat=True).distinct()
        for role in roles:
            if role:
                stats_by_role[role] = UserModel.objects.filter(role=role).count()
        
        # Compter aussi les superusers comme admins
        superuser_count = UserModel.objects.filter(is_superuser=True).count()
        stats_by_role['admin'] = stats_by_role.get('admin', 0) + superuser_count
        
        return Response({
            'total': total_users,
            'active': active_users,
            'inactive': inactive_users,
            'by_role': stats_by_role,
        })


class AdminDashboardView(APIView):
    """
    GET /api/admin/dashboard/
    Données pour le tableau de bord admin
    """
    permission_classes = [IsAdminPermission]
    
    def get(self, request):
        # Statistiques utilisateurs
        user_stats = {
            'total': UserModel.objects.count(),
            'active': UserModel.objects.filter(is_active=True).count(),
            'new_this_month': UserModel.objects.filter(
                date_joined__month=request.user.date_joined.month
            ).count(),
        }
        
        return Response({
            'users': user_stats,
        })