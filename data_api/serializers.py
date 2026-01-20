from rest_framework import serializers
from .models import EntrepriseEcon


class PingSerializer(serializers.Serializer):
    status = serializers.CharField()


class EntrepriseSerializer(serializers.ModelSerializer):
    """
    Serializer pour la liste des entreprises (tableaux / recherche).
    """
    class Meta:
        model = EntrepriseEcon
        fields = [
            "ent_uuid",
            "id_ent",
            "raison_sociale",
            "secteur_principal_label",
            "taille_entreprise_label",
            "commune_nom",
            "region_nom",
            "est_mpme_formalisee",
            "est_mpme_appuyee_fiere",
        ]


class EntrepriseDashboardSerializer(serializers.Serializer):
    """
    Serializer pour les indicateurs d'agrégation entreprises.
    """
    total_entreprises = serializers.IntegerField()
    total_mpme_formalisees = serializers.IntegerField()
    total_mpme_appuyees_fiere = serializers.IntegerField()
