# data_api/urls_geojson.py

from django.urls import path

# --- Couches de référence (imports directs OK, elles existent) ---
from .views_geojson_ref import (
    AdminRegionGeoJSONView,
    AdminPrefectureGeoJSONView,
    AdminCommuneGeoJSONView,
    AgglomerationGeoJSONView,
    AireProtegeeGeoJSONView,
    EquipementGeoJSONView,
    HabitationDisperseeGeoJSONView,
    HydrographieGeoJSONView,
    LocaliteGeoJSONView,
    OccupationSolGeoJSONView,
    ReseauRoutierGeoJSONView,
    ZoneHumideGeoJSONView,
    ZoneSableuseGeoJSONView,
)

# --- Couches collectées (imports SAFE : si une classe manque, on ne casse pas Django) ---
import data_api.views_geojson_collected as collected


def pick(*names):
    """Retourne la première classe existante parmi names, sinon None."""
    for n in names:
        v = getattr(collected, n, None)
        if v is not None:
            return v
    return None


# Agrieco / core-marts
CepParcellesGeoJSONView = pick("CepParcellesGeoJSONView")
TeteSourceGeoJSONView = pick("TeteSourceGeoJSONView")
# ⚠️ Ici on accepte plusieurs variantes possibles
MeteoStationsGeoJSONView = pick("MeteoStationsGeoJSONView", "MeteoStationGeoJSONView")
OuvragesGeoJSONView = pick("OuvragesGeoJSONView")
CouloirsGeoJSONView = pick("CouloirsGeoJSONView")
ZonesDegradeesGeoJSONView = pick("ZonesDegradeesGeoJSONView", "ZoneDegradeeGeoJSONView")
OrganisationsGeoJSONView = pick("OrganisationsGeoJSONView")
MenagesGeoJSONView = pick("MenagesGeoJSONView")
MarchesGeoJSONView = pick("MarchesGeoJSONView")

# Fiere / core-marts
EntreprisesGeoJSONView = pick("EntreprisesGeoJSONView")
FormationsGeoJSONView = pick("FormationsGeoJSONView")
SortantsGeoJSONView = pick("SortantsGeoJSONView")

# All
AllLayersGeoJSONView = pick("AllLayersGeoJSONView")


urlpatterns = [
    # === Volet cartographie : couches de base (schéma ref) ===
    path("carto/admin-region/", AdminRegionGeoJSONView.as_view(), name="carto_admin_region"),
    path("carto/admin-prefecture/", AdminPrefectureGeoJSONView.as_view(), name="carto_admin_prefecture"),
    path("carto/admin-commune/", AdminCommuneGeoJSONView.as_view(), name="carto_admin_commune"),
    path("carto/agglomeration/", AgglomerationGeoJSONView.as_view(), name="carto_agglomeration"),
    path("carto/aire-protegee/", AireProtegeeGeoJSONView.as_view(), name="carto_aire_protegee"),
    path("carto/equipement/", EquipementGeoJSONView.as_view(), name="carto_equipement"),
    path("carto/habitation-dispersee/", HabitationDisperseeGeoJSONView.as_view(), name="carto_habitation_dispersee"),
    path("carto/localite/", LocaliteGeoJSONView.as_view(), name="carto_localite"),
    path("carto/occupation-sol/", OccupationSolGeoJSONView.as_view(), name="carto_occupation_sol"),
    path("carto/zone-humide/", ZoneHumideGeoJSONView.as_view(), name="carto_zone_humide"),
    path("carto/zone-sableuse/", ZoneSableuseGeoJSONView.as_view(), name="carto_zone_sableuse"),
    path("carto/hydrographie/", HydrographieGeoJSONView.as_view(), name="carto_hydrographie"),
    path("carto/reseau-routier/", ReseauRoutierGeoJSONView.as_view(), name="carto_reseau_routier"),
]


def add(url, view, name):
    """Ajoute une route seulement si la classe view existe."""
    if view is not None:
        urlpatterns.append(path(url, view.as_view(), name=name))


# === Couches collectées AGRIECO ===
add("carto/cep-parcelles/", CepParcellesGeoJSONView, "carto_cep_parcelles")
add("carto/tete-source/", TeteSourceGeoJSONView, "carto_tete_source")
add("carto/meteo-stations/", MeteoStationsGeoJSONView, "carto_meteo_stations")
add("carto/ouvrages/", OuvragesGeoJSONView, "carto_ouvrages")
add("carto/couloirs/", CouloirsGeoJSONView, "carto_couloirs")
add("carto/zone-degradee/", ZonesDegradeesGeoJSONView, "carto_zone_degradee")
add("carto/agr-organisations/", OrganisationsGeoJSONView, "carto_agr_organisations")
add("carto/agr-menages/", MenagesGeoJSONView, "carto_agr_menages")
add("carto/marches/", MarchesGeoJSONView, "carto_marches")

# === Couches collectées FIERE ===
add("carto/entreprises/", EntreprisesGeoJSONView, "carto_entreprises")
add("carto/formations/", FormationsGeoJSONView, "carto_formations")
add("carto/sortants/", SortantsGeoJSONView, "carto_sortants")

# === Toutes les couches ===
add("carto/all-layers/", AllLayersGeoJSONView, "carto_all_layers")
