# data_api/urls.py

from django.urls import path, include

from .views import (
    PingView,
    EntrepriseListView,
    EntrepriseAggregationView,
    ActeurParticipationListView,
    ActeurParticipationAggregatesView,
    AgrComiteListView,
    AgrComiteAggregatesView,
    AgrMenageListView,
    AgrMenageAggregatesView,
    AgrOrganisationListView,
    AgrOrganisationAggregatesView,
    CepParcelleListView,
    CepParcelleAggregatesView,
    CouloirListView,
    CouloirAggregatesView,
    EntEmploiDomListView,
    EntEmploiDomAggregatesView,
    EntInsertionDomListView,
    EntInsertionDomAggregatesView,
    FiereSuiviSortantListView,
    FiereSuiviSortantAggregatesView,
    FormationEcoCatListView,
    FormationEcoCatAggregatesView,
    IntrantDistributionListView,
    IntrantDistributionAggregatesView,
    MarcheListView,
    MarcheAggregatesView,
    MeteoMesureListView,
    MeteoMesureAggregatesView,
    MeteoStationListView,
    MeteoStationAggregatesView,
    OuvrageListView,
    OuvrageAggregatesView,
    PratiquesAgroParcelleListView,
    PratiquesAgroParcelleAggregatesView,
    TeteSourceListView,
    TeteSourceAggregatesView,
    ZoneDegradeeListView,
    ZoneDegradeeAggregatesView,
)

urlpatterns = [
    # --- API data (tableaux / stats) ---
    path("ping/", PingView.as_view(), name="data_ping"),

    path("entreprises/", EntrepriseListView.as_view(), name="entreprise-list"),
    path("entreprises/stats/", EntrepriseAggregationView.as_view(), name="entreprise-aggregations"),

    path("acteurs-participation/", ActeurParticipationListView.as_view(), name="acteur-participation-list"),
    path("acteurs-participation/stats/", ActeurParticipationAggregatesView.as_view(), name="acteur-participation-aggregates"),

    path("agr-comites/", AgrComiteListView.as_view(), name="agr-comite-list"),
    path("agr-comites/stats/", AgrComiteAggregatesView.as_view(), name="agr-comite-aggregates"),

    path("agr-menages/", AgrMenageListView.as_view(), name="agr-menage-list"),
    path("agr-menages/stats/", AgrMenageAggregatesView.as_view(), name="agr-menage-aggregates"),

    path("agr-organisations/", AgrOrganisationListView.as_view(), name="agr-organisation-list"),
    path("agr-organisations/stats/", AgrOrganisationAggregatesView.as_view(), name="agr-organisation-aggregates"),

    path("cep-parcelles/", CepParcelleListView.as_view(), name="cep-parcelle-list"),
    path("cep-parcelles/stats/", CepParcelleAggregatesView.as_view(), name="cep-parcelle-aggregates"),

    path("couloirs/", CouloirListView.as_view(), name="couloir-list"),
    path("couloirs/stats/", CouloirAggregatesView.as_view(), name="couloir-aggregates"),

    path("ent-emplois-dom/", EntEmploiDomListView.as_view(), name="ent-emploi-dom-list"),
    path("ent-emplois-dom/stats/", EntEmploiDomAggregatesView.as_view(), name="ent-emploi-dom-aggregates"),

    path("ent-insertions-dom/", EntInsertionDomListView.as_view(), name="ent-insertion-dom-list"),
    path("ent-insertions-dom/stats/", EntInsertionDomAggregatesView.as_view(), name="ent-insertion-dom-aggregates"),

    path("fiere-suivi-sortants/", FiereSuiviSortantListView.as_view(), name="fiere-suivi-sortant-list"),
    path("fiere-suivi-sortants/stats/", FiereSuiviSortantAggregatesView.as_view(), name="fiere-suivi-sortant-aggregates"),

    path("formations-eco-cat/", FormationEcoCatListView.as_view(), name="formation-eco-cat-list"),
    path("formations-eco-cat/stats/", FormationEcoCatAggregatesView.as_view(), name="formation-eco-cat-aggregates"),

    path("intrants-distribution/", IntrantDistributionListView.as_view(), name="intrant-distribution-list"),
    path("intrants-distribution/stats/", IntrantDistributionAggregatesView.as_view(), name="intrant-distribution-aggregates"),

    path("marches/", MarcheListView.as_view(), name="data_marches"),
    path("marches/stats/", MarcheAggregatesView.as_view(), name="marche-aggregates"),

    path("meteo/mesures/", MeteoMesureListView.as_view(), name="meteo-mesure-list"),
    path("meteo/mesures/stats/", MeteoMesureAggregatesView.as_view(), name="meteo-mesure-aggregates"),

    path("meteo/stations/", MeteoStationListView.as_view(), name="meteo-station-list"),
    path("meteo/stations/stats/", MeteoStationAggregatesView.as_view(), name="meteo-station-aggregates"),

    path("ouvrages/", OuvrageListView.as_view(), name="ouvrage-list"),
    path("ouvrages/stats/", OuvrageAggregatesView.as_view(), name="ouvrage-aggregates"),

    path("pratiques-agro-parcelle/", PratiquesAgroParcelleListView.as_view(), name="pratiques-agro-parcelle-list"),
    path("pratiques-agro-parcelle/stats/", PratiquesAgroParcelleAggregatesView.as_view(), name="pratiques-agro-parcelle-aggregates"),

    path("tete-source/", TeteSourceListView.as_view(), name="tete-source-list"),
    path("tete-source/stats/", TeteSourceAggregatesView.as_view(), name="tete-source-aggregates"),

    path("zone-degradee/", ZoneDegradeeListView.as_view(), name="zone-degradee-list"),
    path("zone-degradee/stats/", ZoneDegradeeAggregatesView.as_view(), name="zone-degradee-aggregates"),

    # --- Volet carto : on inclut UNIQUEMENT urls_geojson ---
    path("", include("data_api.urls_geojson")),
]
