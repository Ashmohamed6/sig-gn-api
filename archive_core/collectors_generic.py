from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any

from django.db import connection, transaction

from .models import (
    ArchiveMetricDefinition,
    ArchiveSnapshot,
    PeriodGranularity,
    ProjectScope,
    TrendPolarity,
    ValueBehavior,
)


@dataclass(frozen=True)
class TableConfig:
    dataset_codes: tuple[str, ...]
    project_scope: str
    table_name: str
    metric_prefix: str
    label_prefix: str
    source_dataset: str
    source_reference: str
    region_field: str = "id_region"
    entity_fields: tuple[str, ...] = ()
    date_fields: tuple[str, ...] = ()
    year_fields: tuple[str, ...] = ()
    payload_date_fields: tuple[str, ...] = ()
    payload_year_fields: tuple[str, ...] = ()
    skip_fields: tuple[str, ...] = ()
    include_fields: tuple[str, ...] = ()
    custom_sql: str | None = None
    custom_region_filter_sql: str | None = None


@dataclass(frozen=True)
class RuntimeMetric:
    column_name: str
    metric: ArchiveMetricDefinition
    aggregation: str


TABLE_CONFIGS: tuple[TableConfig, ...] = (
    TableConfig(("agr-menages",), ProjectScope.AGRIECO, "agr_menage", "agrieco_menage", "AGRIECO menages", "agr-menages", "core.agr_menage", entity_fields=("id_menage", "nom_chef_menage"), date_fields=("valid_from",), payload_date_fields=("today", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-organisations",), ProjectScope.AGRIECO, "agr_organisation", "agrieco_organisation", "AGRIECO organisations", "agr-organisations", "core.agr_organisation", entity_fields=("id_org", "nom_org"), date_fields=("valid_from",), payload_date_fields=("today", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-comites",), ProjectScope.AGRIECO, "agr_comite", "agrieco_comite", "AGRIECO comites", "agr-comites", "core.agr_comite", entity_fields=("id_comite", "nom_comite"), date_fields=("valid_from",), year_fields=("annee_creation",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("annee_creation",)),
    TableConfig(("cep-parcelles",), ProjectScope.AGRIECO, "cep_parcelle", "agrieco_parcelle", "AGRIECO parcelles", "cep-parcelles", "core.cep_parcelle", entity_fields=("id_cep",), year_fields=("campagne_yyyy",), payload_year_fields=("campagne", "campagne_yyyy"), skip_fields=("surface_decl",)),
    TableConfig(("agr-pratiques-rendements",), ProjectScope.AGRIECO, "pratiques_agro_parcelle", "agrieco_pratique", "AGRIECO pratiques", "agr-pratiques-rendements", "core.pratiques_agro_parcelle", entity_fields=("id_cep",), date_fields=("valid_from",), year_fields=("campagne_yyyy",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("campagne", "campagne_yyyy")),
    TableConfig(("agr-intrants-comptoirs", "agr-intrants-distribution"), ProjectScope.AGRIECO, "intrant_distribution", "agrieco_intrant", "AGRIECO intrants", "agr-intrants-distribution", "core.intrant_distribution", entity_fields=("type_intrant",), date_fields=("valid_from",), year_fields=("campagne_yyyy",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("campagne_intrant", "campagne_yyyy")),
    TableConfig(("agr-intrants-comptoirs",), ProjectScope.AGRIECO, "marche", "agrieco_marche", "AGRIECO marches", "agr-intrants-comptoirs", "core.marche", entity_fields=("nom_comptoir",), date_fields=("valid_from",), payload_date_fields=("today", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-ouvrages",), ProjectScope.AGRIECO, "ouvrage", "agrieco_ouvrage", "AGRIECO ouvrages", "agr-ouvrages", "core.ouvrage", entity_fields=("id_ouvrage", "code_ouvrage"), date_fields=("valid_from",), payload_date_fields=("today", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-couloirs",), ProjectScope.AGRIECO, "couloir", "agrieco_couloir", "AGRIECO couloirs", "agr-couloirs", "core.couloir", entity_fields=("id_couloir", "nom_couloir"), date_fields=("valid_from",), payload_date_fields=("today", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-stations-pluie",), ProjectScope.AGRIECO, "meteo_station", "agrieco_station", "AGRIECO stations", "agr-stations-pluie", "core.meteo_station", entity_fields=("code_station", "nom_station"), date_fields=("date_mise_service", "valid_from"), payload_date_fields=("today", "date_mise_service", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-stations-pluie", "agr-mesures-meteo"), ProjectScope.AGRIECO, "meteo_mesure", "agrieco_meteo", "AGRIECO meteo", "agr-mesures-meteo", "core.meteo_mesure", entity_fields=("code_station",), date_fields=("date_obs", "valid_from"), payload_date_fields=("today", "date_obs", "_submission_time", "submission_time", "start")),
    TableConfig(("agr-tetes-sources",), ProjectScope.AGRIECO, "tete_source", "agrieco_tete_source", "AGRIECO tetes de source", "agr-tetes-sources", "core.tete_source", entity_fields=("id_ts",), date_fields=("valid_from",), year_fields=("annee_protection",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("annee_protection",)),
    TableConfig(("agr-zones-degradees",), ProjectScope.AGRIECO, "zone_degradee", "agrieco_zone", "AGRIECO zones degradees", "agr-zones-degradees", "core.zone_degradee", entity_fields=("id_zone",), date_fields=("valid_from",), year_fields=("annee_plantation",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("annee_plantation",), skip_fields=("surface_restaur_ha",)),
    TableConfig(("fiere-suivi-sortants",), ProjectScope.FIERE, "fiere_suivi_sortant", "fiere_sortant", "FIERE sortants", "fiere-suivi-sortants", "core.fiere_suivi_sortant", entity_fields=("id_sortant", "nom_sortant"), date_fields=("date_suivi", "date_fin_formation", "valid_from"), payload_date_fields=("today", "date_suivi", "date_fin_formation", "_submission_time", "submission_time", "start")),
    TableConfig(("fiere-entreprises",), ProjectScope.FIERE, "entreprise_econ", "fiere_entreprise", "FIERE entreprises", "fiere-entreprises", "core.entreprise_econ", entity_fields=("id_ent", "raison_sociale"), date_fields=("valid_from",), year_fields=("annee_creation",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("annee_creation",)),
    TableConfig(("fiere-formations",), ProjectScope.FIERE, "formation_eco", "fiere_formation", "FIERE formations", "fiere-formations", "core.formation_eco", entity_fields=("id_formation", "intitule_formation"), date_fields=("date_debut", "date_fin", "valid_from"), payload_date_fields=("today", "date_debut", "date_fin", "_submission_time", "submission_time", "start")),
    TableConfig(("fiere-emploi-insertion",), ProjectScope.FIERE, "ent_emploi", "fiere_emploi", "FIERE emploi", "fiere-emploi-insertion", "core.ent_emploi", entity_fields=("id_ent", "raison_sociale"), date_fields=("valid_from",), year_fields=("annee_ref",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("annee_ref",)),
    TableConfig(("fiere-emploi-insertion",), ProjectScope.FIERE, "ent_insertion", "fiere_insertion", "FIERE insertion", "fiere-emploi-insertion", "core.ent_insertion", entity_fields=("id_ent", "raison_sociale"), date_fields=("valid_from",), year_fields=("annee_ref",), payload_date_fields=("today", "_submission_time", "submission_time", "start"), payload_year_fields=("annee_ref",)),
    TableConfig(("fiere-participation",), ProjectScope.FIERE, "acteur_participation", "fiere_participation", "FIERE participation", "fiere-participation", "core.acteur_participation", entity_fields=("id_ent", "nom_acteur"), date_fields=("date_derniere_part", "valid_from"), payload_date_fields=("today", "date_derniere_part", "_submission_time", "submission_time", "start")),
    TableConfig(("fiere-formations",), ProjectScope.FIERE, "formation_part_cat", "fiere_formation_cat", "FIERE formations categories", "fiere-formations", "core.formation_part_cat", region_field="region_id", entity_fields=("entity_key",), date_fields=("valid_from", "parent_valid_from", "imported_at"), include_fields=("nb_part_cat", "nb_part_fem_cat", "nb_part_jeunes_cat"), custom_sql="""
        SELECT f.id_region AS region_id, COALESCE(f.id_formation, '') AS entity_key,
               pc.valid_from, f.valid_from AS parent_valid_from, f.imported_at, f.raw_payload,
               pc.nb_part_cat, pc.nb_part_fem_cat, pc.nb_part_jeunes_cat
        FROM core.formation_part_cat pc
        JOIN core.formation_eco f ON f.formation_uuid = pc.formation_uuid
        WHERE f.project_code = %s
        {region_filter}
    """, custom_region_filter_sql="f.id_region = %s"),
    TableConfig(("fiere-emploi-insertion", "fiere-emploi-domaines"), ProjectScope.FIERE, "ent_emploi_dom", "fiere_emploi_dom", "FIERE emploi domaines", "fiere-emploi-domaines", "core.ent_emploi_dom", region_field="region_id", entity_fields=("entity_key",), date_fields=("valid_from", "parent_valid_from", "imported_at"), include_fields=("nb_empl_dom", "nb_empl_fem_dom", "nb_empl_jeunes_dom", "nb_empl_pvh_dom"), custom_sql="""
        SELECT e.id_region AS region_id, COALESCE(e.id_ent, '') AS entity_key,
               d.valid_from, e.valid_from AS parent_valid_from, e.imported_at, e.raw_payload,
               d.nb_empl_dom, d.nb_empl_fem_dom, d.nb_empl_jeunes_dom, d.nb_empl_pvh_dom
        FROM core.ent_emploi_dom d
        JOIN core.ent_emploi e ON e.emploi_uuid = d.emploi_uuid
        WHERE e.project_code = %s
        {region_filter}
    """, custom_region_filter_sql="e.id_region = %s"),
    TableConfig(("fiere-emploi-insertion", "fiere-insertion-domaines"), ProjectScope.FIERE, "ent_insertion_dom", "fiere_insertion_dom", "FIERE insertion domaines", "fiere-insertion-domaines", "core.ent_insertion_dom", region_field="region_id", entity_fields=("entity_key",), date_fields=("valid_from", "parent_valid_from", "imported_at"), include_fields=("nb_ins_dom", "nb_ins_fem_dom", "nb_ins_jeunes_dom", "duree_insertion_mois", "nb_ins_pvh_dom"), custom_sql="""
        SELECT e.id_region AS region_id, COALESCE(e.id_ent, '') AS entity_key,
               d.valid_from, e.valid_from AS parent_valid_from, e.imported_at, e.raw_payload,
               d.nb_ins_dom, d.nb_ins_fem_dom, d.nb_ins_jeunes_dom, d.duree_insertion_mois, d.nb_ins_pvh_dom
        FROM core.ent_insertion_dom d
        JOIN core.ent_insertion e ON e.insertion_uuid = d.insertion_uuid
        WHERE e.project_code = %s
        {region_filter}
    """, custom_region_filter_sql="e.id_region = %s"),
)

# ---------------------------------------------------------------------------
# Human-readable column labels for the frontend
# ---------------------------------------------------------------------------
COLUMN_LABELS: dict[str, str] = {
    # Identifiants
    "id_cep": "ID CEP",
    "id_menage": "ID m\u00e9nage",
    "id_org": "ID organisation",
    "id_comite": "ID comit\u00e9",
    "id_ouvrage": "ID ouvrage",
    "id_couloir": "ID couloir",
    "id_zone": "ID zone",
    "id_ts": "ID t\u00eate de source",
    "id_ent": "ID entreprise",
    "id_sortant": "ID sortant",
    "id_formation": "ID formation",
    "code_station": "Code station",
    "code_ouvrage": "Code ouvrage",
    # Noms
    "nom_chef_menage": "Nom chef de m\u00e9nage",
    "nom_org": "Nom organisation",
    "nom_comite": "Nom comit\u00e9",
    "nom_couloir": "Nom couloir",
    "nom_station": "Nom station",
    "nom_comptoir": "Nom comptoir",
    "nom_sortant": "Nom sortant",
    "nom_acteur": "Nom acteur",
    "raison_sociale": "Raison sociale",
    "intitule_formation": "Intitul\u00e9 formation",
    # G\u00e9ographiques
    "id_region": "R\u00e9gion",
    "id_prefecture": "Pr\u00e9fecture",
    "id_commune": "Commune",
    "localite": "Localit\u00e9",
    # AGRIECO - Parcelles CEP
    "campagne": "Campagne",
    "campagne_yyyy": "Ann\u00e9e campagne",
    "filiere": "Fili\u00e8re",
    "culture_princ": "Culture principale",
    "cultures_assoc": "Cultures associ\u00e9es",
    "surface_ha": "Surface (ha)",
    "surface_decl": "Surface d\u00e9clar\u00e9e (ha)",
    "production_totale": "Production totale (t)",
    "rendement_calc": "Rendement calcul\u00e9 (t/ha)",
    "rendement_saisi": "Rendement saisi (t/ha)",
    "rendement_observe": "Rendement observ\u00e9",
    "menages_ben": "M\u00e9nages b\u00e9n\u00e9ficiaires",
    "nb_paysans_relais": "Paysans relais",
    "pratiques_agroeco": "Pratiques agro\u00e9cologiques",
    "nb_annees_pratiques": "Ann\u00e9es de pratique",
    # AGRIECO - M\u00e9nages
    "nb_personnes": "Nombre de personnes",
    "nb_enfants_u5": "Enfants < 5 ans",
    "nb_seances_total": "S\u00e9ances total",
    "annees_utilisation_foyer": "Ann\u00e9es utilisation foyer",
    "type_menage": "Type de m\u00e9nage",
    # AGRIECO - Organisations
    "nb_membres_total": "Membres total",
    "nb_membres_femmes": "Membres femmes",
    "nb_membres_jeunes": "Membres jeunes",
    "nb_planteurs_accompagnes": "Planteurs accompagn\u00e9s",
    "nb_producteurs_semenciers": "Producteurs semenciers",
    "nb_banques_semences": "Banques de semences",
    "nb_bovins": "Bovins",
    "nb_ovins": "Ovins",
    "nb_caprins": "Caprins",
    "nb_ruches_ken": "Ruches kenyanes",
    "nb_ruches_lang": "Ruches Langstroth",
    "nb_emplois_verts": "Emplois verts",
    # AGRIECO - Comit\u00e9s
    "nb_reunions_12m": "R\u00e9unions (12 mois)",
    "nb_sensib_12m": "Sensibilisations (12 mois)",
    "nb_conflits_12m": "Conflits (12 mois)",
    "nb_conflits_regles": "Conflits r\u00e9gl\u00e9s",
    "nb_techniciens_total": "Techniciens total",
    "annee_creation": "Ann\u00e9e de cr\u00e9ation",
    # AGRIECO - Intrants
    "type_intrant": "Type d'intrant",
    "quantite": "Quantit\u00e9",
    "menages_ben_intr": "M\u00e9nages b\u00e9n\u00e9ficiaires intrants",
    "campagne_intrant": "Campagne intrant",
    # AGRIECO - Couloirs
    "longueur_km": "Longueur (km)",
    "largeur_m": "Largeur (m)",
    # AGRIECO - Stations m\u00e9t\u00e9o
    "pluie_mm": "Pluie (mm)",
    "t_min": "Temp\u00e9rature min (\u00b0C)",
    "t_max": "Temp\u00e9rature max (\u00b0C)",
    "date_obs": "Date observation",
    "date_mise_service": "Date mise en service",
    # AGRIECO - T\u00eates de sources
    "pop_desservie": "Population desservie",
    "annee_protection": "Ann\u00e9e protection",
    # AGRIECO - Ouvrages
    "longueur_anti_m": "Longueur anti\u00e9rosive (m)",
    "surface_couv_ha": "Surface couverture (ha)",
    # AGRIECO - Zones d\u00e9grad\u00e9es
    "surface_degrad_ha": "Surface d\u00e9grad\u00e9e (ha)",
    "surface_restaur_ha": "Surface restaur\u00e9e (ha)",
    "nb_plants": "Nombre de plants",
    "densite_plants_ha": "Densit\u00e9 (plants/ha)",
    "taux_survie_pct": "Taux de survie (%)",
    "nb_terrasses": "Nombre de terrasses",
    "longueur_terr_m": "Longueur terrasses (m)",
    "annee_plantation": "Ann\u00e9e de plantation",
    # FIERE - Sortants
    "date_suivi": "Date de suivi",
    "date_fin_formation": "Date fin formation",
    "sexe": "Sexe",
    "age": "Age",
    "pvh": "Personne vivant avec un handicap",
    # FIERE - Entreprises
    "effectif_total": "Effectif total",
    "ca_approx": "Chiffre d'affaires approx.",
    "taille_entreprise": "Taille entreprise",
    # FIERE - Formations
    "participants_total": "Participants total",
    "participants_femmes": "Participants femmes",
    "participants_jeunes": "Participants jeunes",
    "participants_pvh": "Participants PVH",
    "duree_jours": "Dur\u00e9e (jours)",
    "participants_acheve": "Participants ayant achev\u00e9",
    "date_debut": "Date d\u00e9but",
    "date_fin": "Date fin",
    # FIERE - Emploi/insertion
    "emplois_total": "Emplois total",
    "emplois_femmes": "Emplois femmes",
    "emplois_jeunes": "Emplois jeunes",
    "insert_total": "Insertions total",
    "insert_femmes": "Insertions femmes",
    "insert_jeunes": "Insertions jeunes",
    "annee_ref": "Ann\u00e9e de r\u00e9f\u00e9rence",
    # FIERE - Participation
    "nb_part_12m": "Participations (12 mois)",
    "date_derniere_part": "Date derni\u00e8re participation",
    # FIERE - Sous-tables
    "nb_part_cat": "Participants par cat\u00e9gorie",
    "nb_part_fem_cat": "Participants femmes par cat\u00e9gorie",
    "nb_part_jeunes_cat": "Participants jeunes par cat\u00e9gorie",
    "nb_empl_dom": "Emplois par domaine",
    "nb_empl_fem_dom": "Emplois femmes par domaine",
    "nb_empl_jeunes_dom": "Emplois jeunes par domaine",
    "nb_empl_pvh_dom": "Emplois PVH par domaine",
    "nb_ins_dom": "Insertions par domaine",
    "nb_ins_fem_dom": "Insertions femmes par domaine",
    "nb_ins_jeunes_dom": "Insertions jeunes par domaine",
    "duree_insertion_mois": "Dur\u00e9e insertion (mois)",
    "nb_ins_pvh_dom": "Insertions PVH par domaine",
    # Temporal
    "valid_from": "Date de validit\u00e9",
    "date_collecte": "Date de collecte",
}

# Columns to never show in preview or comparison
# valid_from est une date d'import systeme, pas une date de collecte terrain
HIDDEN_COLUMNS = frozenset({
    "project_code", "raw_payload", "record_source", "import_batch",
    "import_source", "valid_from", "valid_to", "is_active", "created_at",
    "updated_at", "imported_at",
})


def get_column_label(col_name: str) -> str:
    """Return human-readable label for a column name."""
    label = COLUMN_LABELS.get(col_name)
    if label:
        return label
    # Auto-generate from column name
    return col_name.replace("_", " ").strip().capitalize()


DEFAULT_SKIP = frozenset({"project_code", "id_region", "id_prefecture", "id_commune", "is_active", "created_at", "updated_at", "imported_at", "valid_from", "valid_to", "record_source", "raw_payload", "import_batch", "import_source"})
DEFAULT_PAYLOAD_DATES = ("today", "date_obs", "date_suivi", "date_derniere_part", "date_fin_formation", "date_debut", "date_fin", "date_mise_service", "submission_time", "_submission_time", "start", "end")
DEFAULT_PAYLOAD_YEARS = ("campagne", "campagne_yyyy", "annee_ref", "annee_creation", "annee_plantation", "annee_protection")
NUMERIC_TYPES = frozenset({"integer", "bigint", "smallint", "numeric", "double precision", "real", "boolean"})
TRUTHY = frozenset({"1", "true", "t", "yes", "y", "oui", "vrai", "x"})
TABLE_COLS_CACHE: dict[str, dict[str, str]] = {}
MAX_SNAPSHOT_ABS = Decimal("9999999999999999.9999")
SNAPSHOT_QUANT = Decimal("0.0001")


def _quote(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _fetch_rows(sql: str, params: list[Any]) -> list[dict[str, Any]]:
    with connection.cursor() as c:
        c.execute(sql, params)
        cols = [col[0] for col in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]


def _table_cols(table: str) -> dict[str, str]:
    cached = TABLE_COLS_CACHE.get(table)
    if cached is not None:
        return cached
    with connection.cursor() as c:
        c.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema='core' AND table_name=%s
            ORDER BY ordinal_position
        """, [table])
        rows = c.fetchall()
    mapping = {str(n): str(t).lower() for n, t in rows}
    TABLE_COLS_CACHE[table] = mapping
    return mapping


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit() and len(text) == 4:
        y = int(text)
        if 1900 <= y <= 2100:
            return date(y, 1, 1)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _parse_year(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.year
    if isinstance(value, date):
        return value.year
    if isinstance(value, Decimal) and value == value.to_integral_value():
        y = int(value)
        return y if 1900 <= y <= 2100 else None
    text = str(value).strip()
    if text.isdigit() and len(text) == 4:
        y = int(text)
        return y if 1900 <= y <= 2100 else None
    return None


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _slug(prefix: str, col: str) -> str:
    c = re.sub(r"[^a-z0-9_]+", "_", col.lower())
    c = re.sub(r"_+", "_", c).strip("_")
    code = f"{prefix}_{c}" if prefix else c
    if len(code) <= 80:
        return code
    h = hashlib.sha1(code.encode("utf-8")).hexdigest()[:8]
    return f"{code[:71]}_{h}"


def _label(cfg: TableConfig, col: str) -> str:
    txt = re.sub(r"\s+", " ", col.replace("_", " ")).strip().capitalize()
    out = f"{cfg.label_prefix} - {txt}"
    return out[:160]


def _unit(col: str) -> str:
    n = col.lower()
    if "pct" in n or "taux" in n or "pourcent" in n:
        return "%"
    if n.endswith("_ha"):
        return "ha"
    if n.endswith("_km"):
        return "km"
    if n.endswith("_mm"):
        return "mm"
    if n.endswith("_m"):
        return "m"
    if "jours" in n:
        return "jours"
    if n == "age":
        return "ans"
    return ""


def _polarity(col: str) -> str:
    n = col.lower()
    if any(k in n for k in ("conflit", "non_conf", "contrainte", "raison_non", "inactif")):
        return TrendPolarity.LOWER_IS_BETTER
    return TrendPolarity.HIGHER_IS_BETTER


def _agg(col: str, dtype: str) -> str:
    if dtype == "boolean":
        return "count_true"
    n = col.lower()
    if any(k in n for k in ("pct", "taux", "ratio", "moy", "satisfaction", "niveau")):
        return "avg"
    return "sum"


def _to_dec(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _normalize_snapshot_value(value: Decimal) -> Decimal | None:
    try:
        normalized = value.quantize(SNAPSHOT_QUANT)
    except (InvalidOperation, ValueError):
        return None
    if normalized.copy_abs() > MAX_SNAPSHOT_ABS:
        return None
    return normalized


def _norm_region(value: Any) -> str:
    return str(value or "").strip().upper()


def _norm_entity(value: Any) -> str:
    return str(value or "").strip().upper()


def _indicator_cols(cfg: TableConfig) -> list[tuple[str, str]]:
    if cfg.custom_sql:
        return [(c, "numeric") for c in cfg.include_fields]
    cols = _table_cols(cfg.table_name)
    skip = {s.lower() for s in cfg.skip_fields}
    out: list[tuple[str, str]] = []
    for name, dtype in cols.items():
        low = name.lower()
        if dtype not in NUMERIC_TYPES:
            continue
        if low in DEFAULT_SKIP or low in skip:
            continue
        if low.startswith("id_") or low.endswith("_id") or low.endswith("_uuid") or low == "uuid":
            continue
        if low in {y.lower() for y in cfg.year_fields}:
            continue
        out.append((name, dtype))
    return out


def _ensure_metric(cfg: TableConfig, col: str, dtype: str) -> RuntimeMetric:
    code = _slug(cfg.metric_prefix, col)
    metric, created = ArchiveMetricDefinition.objects.get_or_create(
        metric_code=code,
        defaults={
            "label": _label(cfg, col),
            "description": f"Capture auto depuis {cfg.source_reference} avec priorite date collecte (today/date_*).",
            "project_scope": cfg.project_scope,
            "source_dataset": cfg.source_dataset,
            "unit": _unit(col),
            "value_behavior": ValueBehavior.POINT,
            "trend_polarity": _polarity(col),
            "default_granularity": PeriodGranularity.MONTH,
            "is_active": True,
        },
    )
    updates: list[str] = []
    if not created:
        if not metric.is_active:
            metric.is_active = True
            updates.append("is_active")
        if metric.project_scope != cfg.project_scope and metric.project_scope != ProjectScope.BOTH:
            metric.project_scope = ProjectScope.BOTH
            updates.append("project_scope")
        if not metric.source_dataset:
            metric.source_dataset = cfg.source_dataset
            updates.append("source_dataset")
        if updates:
            metric.save(update_fields=updates)
    return RuntimeMetric(col, metric, _agg(col, dtype))


def _rows(cfg: TableConfig, project: str, region: str | None, cols: list[tuple[str, str]]) -> list[dict[str, Any]]:
    if cfg.custom_sql:
        where = ""
        params: list[Any] = [project]
        if region and cfg.custom_region_filter_sql:
            where = f" AND {cfg.custom_region_filter_sql}"
            params.append(region)
        sql = str(cfg.custom_sql).format(region_filter=where)
        return _fetch_rows(sql, params)

    table_cols = _table_cols(cfg.table_name)
    fields: list[str] = []
    for name in [cfg.region_field, *cfg.entity_fields, *cfg.date_fields, *cfg.year_fields, "valid_from", "imported_at", "created_at", "updated_at", "raw_payload", *[n for n, _ in cols]]:
        if name in table_cols and name not in fields:
            fields.append(name)
    if not fields:
        return []
    sql = f"SELECT {', '.join(_quote(f) for f in fields)} FROM core.{_quote(cfg.table_name)} WHERE project_code = %s"
    params: list[Any] = [project]
    if region and cfg.region_field in table_cols:
        sql += f" AND COALESCE({_quote(cfg.region_field)}, '') = %s"
        params.append(region)
    return _fetch_rows(sql, params)


def _obs_period(row: dict[str, Any], cfg: TableConfig) -> tuple[int, int, bool] | None:
    payload = _payload_dict(row.get("raw_payload"))
    seen: set[str] = set()
    for key in [*cfg.payload_date_fields, *DEFAULT_PAYLOAD_DATES]:
        if key in seen:
            continue
        seen.add(key)
        dt = _parse_date(payload.get(key))
        if dt is not None:
            return dt.year, dt.month, True
    for key in cfg.date_fields:
        dt = _parse_date(row.get(key))
        if dt is not None:
            return dt.year, dt.month, True
    seen_year: set[str] = set()
    for key in [*cfg.payload_year_fields, *DEFAULT_PAYLOAD_YEARS]:
        if key in seen_year:
            continue
        seen_year.add(key)
        y = _parse_year(payload.get(key))
        if y is not None:
            return y, 0, False
    for key in cfg.year_fields:
        y = _parse_year(row.get(key))
        if y is not None:
            return y, 0, False
    for key in ("valid_from", "imported_at", "created_at", "updated_at"):
        dt = _parse_date(row.get(key))
        if dt is not None:
            return dt.year, dt.month, True
    return None


def _val(raw: Any, agg: str) -> Decimal | None:
    if raw is None:
        return None
    if agg == "count_true":
        if isinstance(raw, bool):
            return Decimal("1") if raw else None
        return Decimal("1") if str(raw).strip().lower() in TRUTHY else None
    if isinstance(raw, bool):
        return Decimal("1") if raw else Decimal("0")
    try:
        return _to_dec(raw)
    except Exception:
        return None


def _entity_keys(row: dict[str, Any], cfg: TableConfig) -> list[str]:
    keys: list[str] = []
    for key in cfg.entity_fields:
        val = _norm_entity(row.get(key))
        if not val or val in keys:
            continue
        keys.append(val)

    if len(keys) >= 2:
        combined = f"{keys[0]} | {keys[1]}"
        if combined not in keys:
            keys.append(combined)

    return keys


def _upsert_metric_rows(metric: ArchiveMetricDefinition, rows: list[dict[str, Any]], captured_by) -> tuple[int, int]:
    created = 0
    updated = 0
    with transaction.atomic():
        for r in rows:
            _, was_created = ArchiveSnapshot.objects.update_or_create(
                metric=metric,
                project_code=r["project_code"],
                region_id=r["region_id"],
                entity_key=r["entity_key"],
                granularity=r["granularity"],
                period_year=r["period_year"],
                period_month=r["period_month"],
                defaults={
                    "value": r["value"],
                    "source_dataset": r["source_dataset"],
                    "source_reference": r["source_reference"],
                    "metadata": r["metadata"],
                    "captured_by": captured_by,
                },
            )
            if was_created:
                created += 1
            else:
                updated += 1
    return created, updated


def capture_generic_archive_snapshots(
    *,
    project_code: str,
    region_id: str | None,
    dataset_code: str | None,
    captured_by,
) -> dict[str, Any]:
    project = str(project_code or "").strip().upper()
    region = _norm_region(region_id)
    dataset = str(dataset_code or "").strip().lower()

    selected = [
        cfg
        for cfg in TABLE_CONFIGS
        if cfg.project_scope == project and (not dataset or dataset in {d.lower() for d in cfg.dataset_codes})
    ]

    created_total = 0
    updated_total = 0
    captured: list[dict[str, Any]] = []

    for cfg in selected:
        cols = _indicator_cols(cfg)
        if not cols:
            continue
        runtimes = [_ensure_metric(cfg, col, dtype) for col, dtype in cols]
        try:
            fetched = _rows(cfg, project, region or None, cols)
        except Exception as exc:
            captured.append({"metric_code": f"{cfg.metric_prefix}__error", "rows_prepared": 0, "created": 0, "updated": 0, "source_reference": cfg.source_reference, "error": str(exc)})
            continue

        bucket: dict[tuple[int, str, int, int, str, str], dict[str, Decimal | int]] = defaultdict(lambda: {"sum": Decimal("0"), "count": 0})
        by_metric_id = {rm.metric.id: rm for rm in runtimes}

        for row in fetched:
            obs = _obs_period(row, cfg)
            if obs is None:
                continue
            year, month, has_month = obs
            if year < 2000 or year > 2100:
                continue
            row_region = _norm_region(row.get(cfg.region_field))
            row_entities = _entity_keys(row, cfg)

            # Capture at entity level + rolled-up regional/global totals.
            scopes: set[tuple[str, str]] = {(row_region, ""), ("", "")}
            for entity_key in row_entities:
                scopes.add((row_region, entity_key))
                scopes.add(("", entity_key))

            for rm in runtimes:
                dv = _val(row.get(rm.column_name), rm.aggregation)
                if dv is None:
                    continue
                for scope_region, scope_entity in scopes:
                    yk = (rm.metric.id, PeriodGranularity.YEAR, year, 0, scope_region, scope_entity)
                    bucket[yk]["sum"] = bucket[yk]["sum"] + dv
                    bucket[yk]["count"] = int(bucket[yk]["count"] or 0) + 1
                    if has_month and 1 <= month <= 12:
                        mk = (rm.metric.id, PeriodGranularity.MONTH, year, month, scope_region, scope_entity)
                        bucket[mk]["sum"] = bucket[mk]["sum"] + dv
                        bucket[mk]["count"] = int(bucket[mk]["count"] or 0) + 1

        rows_by_metric: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for (metric_id, granularity, year, month, row_region, row_entity), acc in bucket.items():
            rm = by_metric_id.get(metric_id)
            if rm is None:
                continue
            total = acc["sum"]
            count = int(acc["count"] or 0)
            if rm.aggregation == "avg":
                if count <= 0:
                    continue
                value = total / Decimal(str(count))
            else:
                value = total

            normalized_value = _normalize_snapshot_value(value)
            if normalized_value is None:
                continue

            rows_by_metric[metric_id].append(
                {
                    "project_code": project,
                    "region_id": row_region,
                    "entity_key": row_entity,
                    "granularity": granularity,
                    "period_year": year,
                    "period_month": month,
                    "value": normalized_value,
                    "source_dataset": cfg.source_dataset,
                    "source_reference": cfg.source_reference,
                    "metadata": {"capture_mode": "auto_publish", "collector": "generic_indicator", "aggregation": rm.aggregation, "date_basis": "form_date_or_today", "column_name": rm.column_name},
                }
            )

        for metric_id, metric_rows in rows_by_metric.items():
            if not metric_rows:
                continue
            rm = by_metric_id.get(metric_id)
            if rm is None:
                continue
            created, updated = _upsert_metric_rows(rm.metric, metric_rows, captured_by)
            created_total += created
            updated_total += updated
            captured.append({"metric_code": rm.metric.metric_code, "rows_prepared": len(metric_rows), "created": created, "updated": updated, "source_reference": cfg.source_reference})

    return {"captured_metrics": captured, "created": created_total, "updated": updated_total}
