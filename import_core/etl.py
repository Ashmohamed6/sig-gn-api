from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import connection
from django.utils import timezone

from .csv_parser import CsvParseResult


_WKT_PREFIX_PATTERN = re.compile(r"^(POINT|LINESTRING|POLYGON|MULTI|GEOMETRYCOLLECTION)\s*\(", re.IGNORECASE)


@dataclass(frozen=True)
class DatasetDefinition:
    code: str
    label: str
    stage_table: str
    project_codes: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    identifier_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class StageColumn:
    name: str
    data_type: str
    udt_name: str
    is_nullable: bool
    has_default: bool

    @property
    def is_array(self) -> bool:
        return self.udt_name.startswith("_")


DATASET_DEFINITIONS: tuple[DatasetDefinition, ...] = (
    DatasetDefinition(
        code="agr-menages",
        label="Menages sensibilisation",
        stage_table="agr_menage_raw",
        project_codes=("AGRIECO",),
        aliases=("menages-sensibilisation", "agr_menages", "agr-menage"),
        identifier_fields=("id_menage",),
    ),
    DatasetDefinition(
        code="agr-organisations",
        label="Organisations de producteurs",
        stage_table="agr_org_raw",
        project_codes=("AGRIECO",),
        aliases=("organisations-producteurs", "agr_organisations", "agr-org"),
        identifier_fields=("id_org",),
    ),
    DatasetDefinition(
        code="agr-comites",
        label="Comites de gouvernance",
        stage_table="agr_comite_raw",
        project_codes=("AGRIECO",),
        aliases=("comites-gouvernance-locale", "agr-comite"),
        identifier_fields=("id_comite",),
    ),
    DatasetDefinition(
        code="cep-parcelles",
        label="Parcelles CEP",
        stage_table="agr_cep_raw",
        project_codes=("AGRIECO",),
        aliases=("agr-cep", "parcelles-cep"),
        identifier_fields=("id_cep",),
    ),
    DatasetDefinition(
        code="agr-pratiques-rendements",
        label="Pratiques agroecologiques",
        stage_table="pratiques_agro_raw",
        project_codes=("AGRIECO",),
        aliases=("pratiques-agroecologiques-rendements", "agr-pratiques"),
        identifier_fields=("id_cep", "id_parcelle"),
    ),
    DatasetDefinition(
        code="agr-intrants-comptoirs",
        label="Intrants et comptoirs",
        stage_table="intrant_comptoir_raw",
        project_codes=("AGRIECO",),
        aliases=("intrants-agricoles-comptoirs", "agr-intrants", "agr-marches", "marches"),
        identifier_fields=("id", "submission_uuid"),
    ),
    DatasetDefinition(
        code="agr-intrants-distribution",
        label="Distribution intrants",
        stage_table="intrant_distribution_raw",
        project_codes=("AGRIECO",),
        aliases=("distribution-intrants",),
        identifier_fields=("id_kit", "id_beneficiaire"),
    ),
    DatasetDefinition(
        code="agr-ouvrages",
        label="Ouvrages hydro-agricoles",
        stage_table="ouvrage_raw",
        project_codes=("AGRIECO",),
        aliases=("ouvrages-antierosifs", "ouvrages-antierosifs"),
        identifier_fields=("id_ouvrage", "code_ouvrage"),
    ),
    DatasetDefinition(
        code="agr-couloirs",
        label="Couloirs de transhumance",
        stage_table="couloir_raw",
        project_codes=("AGRIECO",),
        aliases=("couloirs-transhumance",),
        identifier_fields=("id_couloir",),
    ),
    DatasetDefinition(
        code="agr-stations-pluie",
        label="Stations meteo",
        stage_table="meteo_station_raw",
        project_codes=("AGRIECO",),
        aliases=("stations-pluviometriques", "agr-stations-meteo"),
        identifier_fields=("station_id", "code_station"),
    ),
    DatasetDefinition(
        code="agr-mesures-meteo",
        label="Mesures meteo",
        stage_table="meteo_mesure_raw",
        project_codes=("AGRIECO",),
        aliases=("mesures-meteo", "agr-meteo-mesures"),
        identifier_fields=("id_mesure", "station_id"),
    ),
    DatasetDefinition(
        code="agr-tetes-sources",
        label="Tetes de source",
        stage_table="tete_source_raw",
        project_codes=("AGRIECO",),
        aliases=("tetes-sources-protections", "tetes-sources"),
        identifier_fields=("id_ts",),
    ),
    DatasetDefinition(
        code="agr-zones-degradees",
        label="Zones degradees",
        stage_table="zone_degradee_raw",
        project_codes=("AGRIECO",),
        aliases=("zones-degradees-restauration",),
        identifier_fields=("id_zone",),
    ),
    DatasetDefinition(
        code="fiere-suivi-sortants",
        label="Suivi des sortants",
        stage_table="fiere_suivi_sortant_raw",
        project_codes=("FIERE",),
        aliases=("suivi-sortants-formation", "fiere-sortants"),
        identifier_fields=("id_sortant",),
    ),
    DatasetDefinition(
        code="fiere-entreprises",
        label="Entreprises",
        stage_table="fiere_entreprise_raw",
        project_codes=("FIERE",),
        aliases=("entreprises-unites-economiques",),
        identifier_fields=("id_ent",),
    ),
    DatasetDefinition(
        code="fiere-formations",
        label="Formations",
        stage_table="fiere_formation_raw",
        project_codes=("FIERE",),
        aliases=("formations-renforcement-capacites",),
        identifier_fields=("id_formation",),
    ),
    DatasetDefinition(
        code="fiere-emploi-insertion",
        label="Emploi et insertion",
        stage_table="fiere_empins_ent_raw",
        project_codes=("FIERE",),
        aliases=("emploi-insertion-professionnelle", "fiere-empins"),
        identifier_fields=("id_ent", "annee_ref", "periode_ref"),
    ),
    DatasetDefinition(
        code="fiere-emploi-domaines",
        label="Domaines emploi",
        stage_table="fiere_emploi_dom_raw",
        project_codes=("FIERE",),
        aliases=("fiere-emploi-dom",),
        identifier_fields=("emploi_uuid", "domaine_code"),
    ),
    DatasetDefinition(
        code="fiere-insertion-domaines",
        label="Domaines insertion",
        stage_table="fiere_insertion_dom_raw",
        project_codes=("FIERE",),
        aliases=("fiere-insertion-dom",),
        identifier_fields=("insertion_uuid", "domaine_code"),
    ),
    DatasetDefinition(
        code="fiere-participation",
        label="Participation acteurs",
        stage_table="fiere_participation_raw",
        project_codes=("FIERE",),
        aliases=("acteurs-participation", "fiere-acteurs"),
        identifier_fields=("id_ent", "nom_acteur"),
    ),
)


_SYSTEM_COLUMNS = frozenset({
    "raw_uuid", "submission_uuid", "import_batch", "import_source",
    "imported_at", "project_code", "region", "geom", "geom_zone",
    "raw_payload", "enumerator_id", "id_commune",
})

_KOBO_META_COLUMNS = frozenset({
    # Form metadata (no leading underscores in CSV)
    "username", "deviceid", "start", "end", "today",
    # GPS fields (CSV: _gps_point_latitude -> normalized: gps_point_latitude)
    "gps_point", "gps_point_latitude", "gps_point_longitude",
    "gps_point_altitude", "gps_point_precision",
    # Submission metadata: CSV headers have leading underscores stripped by
    # csv_parser.normalize_header_name() -> _uuid becomes uuid, etc.
    "id", "uuid", "submission_time", "validation_status",
    "notes", "status", "submitted_by", "version", "tags", "index",
})


def categorize_column(column_name: str) -> str:
    if column_name in _SYSTEM_COLUMNS:
        return "system"
    if column_name in _KOBO_META_COLUMNS:
        return "kobo_meta"
    return "data"


def normalize_dataset_code(value: str | None) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _build_dataset_lookup() -> dict[str, DatasetDefinition]:
    lookup: dict[str, DatasetDefinition] = {}
    for definition in DATASET_DEFINITIONS:
        lookup[definition.code] = definition
        for alias in definition.aliases:
            lookup[normalize_dataset_code(alias)] = definition
    return lookup


_DATASET_LOOKUP = _build_dataset_lookup()


def resolve_dataset_definition(dataset_code: str, project_code: str | None = None) -> DatasetDefinition | None:
    normalized = normalize_dataset_code(dataset_code)
    definition = _DATASET_LOOKUP.get(normalized)
    if not definition:
        return None

    if project_code and project_code.upper() not in definition.project_codes:
        return None

    return definition


def list_datasets_for_project(project_code: str | None = None) -> list[DatasetDefinition]:
    if not project_code:
        return list(DATASET_DEFINITIONS)
    code = project_code.upper()
    return [d for d in DATASET_DEFINITIONS if code in d.project_codes]


def _quote_ident(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def get_stage_columns(stage_table: str) -> list[StageColumn]:
    sql = """
        SELECT
            column_name,
            data_type,
            udt_name,
            is_nullable,
            column_default
        FROM information_schema.columns
        WHERE table_schema = 'stage'
          AND table_name = %s
        ORDER BY ordinal_position
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [stage_table])
        rows = cursor.fetchall()

    if not rows:
        raise ValueError(f"Table stage introuvable: stage.{stage_table}")

    return [
        StageColumn(
            name=row[0],
            data_type=row[1],
            udt_name=row[2],
            is_nullable=(row[3] == "YES"),
            has_default=(row[4] is not None),
        )
        for row in rows
    ]


def get_stage_primary_key_columns(stage_table: str) -> list[str]:
    sql = """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'stage'
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, [stage_table])
        rows = cursor.fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _parse_int(value: str) -> int:
    return int(value)


def _parse_decimal(value: str) -> Decimal:
    normalized = value.replace(" ", "").replace(",", ".")
    return Decimal(normalized)


def _parse_date(value: str) -> date:
    candidates = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")
    for pattern in candidates:
        try:
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    raise ValueError("date invalide")


def _parse_datetime(value: str) -> datetime:
    clean = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(clean)
    except ValueError:
        pass

    candidates = ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S")
    for pattern in candidates:
        try:
            return datetime.strptime(clean, pattern)
        except ValueError:
            continue

    raise ValueError("datetime invalide")


def _parse_bool(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "vrai", "yes", "oui"}:
        return True
    if lowered in {"0", "false", "faux", "no", "non"}:
        return False
    raise ValueError("booleen invalide")


def _parse_text_array(value: str) -> list[str]:
    text = value.strip()
    if not text:
        return []

    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]

    separator = "|"
    if "|" not in text and "," in text:
        separator = ","

    items = [item.strip() for item in text.split(separator)]
    return [item for item in items if item]


def coerce_value(value: Any, column: StageColumn) -> Any:
    if value is None:
        return None

    text = str(value).strip()
    if text == "":
        return None

    # Kobo exports sometimes emit "0" for unanswered date/datetime fields.
    if text == "0" and (column.data_type == "date" or "timestamp" in column.data_type):
        return None

    try:
        if column.udt_name in {"int2", "int4", "int8"}:
            return _parse_int(text)
        if column.udt_name in {"numeric", "float4", "float8"}:
            return _parse_decimal(text)
        if column.udt_name == "bool":
            return _parse_bool(text)
        if column.data_type == "date":
            return _parse_date(text)
        if "timestamp" in column.data_type:
            return _parse_datetime(text)
        if column.udt_name in {"uuid"}:
            return uuid.UUID(text)
        if column.udt_name.startswith("_"):
            return _parse_text_array(text)
        return text
    except (ValueError, InvalidOperation) as exc:
        raise ValueError(str(exc)) from exc


def _safe_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _kobo_points_from_path(raw: str) -> list[tuple[float, float]]:
    """
    Parse Kobo geotrace/geoshape format:
      "lat lon alt acc; lat lon alt acc; ..."
    Returns points in WKT order (lon, lat).
    """

    points: list[tuple[float, float]] = []
    for segment in str(raw or "").split(";"):
        segment = segment.strip()
        if not segment:
            continue

        parts = [p for p in re.split(r"[,\s]+", segment) if p]
        if len(parts) < 2:
            continue

        lat = _safe_float(parts[0])
        lon = _safe_float(parts[1])
        if lat is None or lon is None:
            continue

        points.append((lon, lat))

    return points


def _build_linestring_wkt(raw: str) -> str | None:
    points = _kobo_points_from_path(raw)
    if len(points) < 2:
        return None
    coords = ", ".join(f"{lon} {lat}" for lon, lat in points)
    return f"LINESTRING({coords})"


def _build_polygon_wkt(raw: str) -> str | None:
    points = _kobo_points_from_path(raw)
    if len(points) < 3:
        return None

    if points[0] != points[-1]:
        points.append(points[0])

    if len(points) < 4:
        return None

    coords = ", ".join(f"{lon} {lat}" for lon, lat in points)
    return f"POLYGON(({coords}))"


def build_geom_sql(raw_geom: str | None, row: dict[str, str]) -> tuple[str, list[Any]] | None:
    geom = str(raw_geom or "").strip()
    if not geom:
        # Kobo exports often provide a combined "gps_point" value: "lat lon alt acc"
        for key in ("gps_point", "grp_loc_gps_point"):
            candidate = str(row.get(key) or "").strip()
            if candidate:
                geom = candidate
                break
    if geom:
        if geom.upper().startswith("SRID="):
            return "ST_GeomFromEWKT(%s)", [geom]
        if _WKT_PREFIX_PATTERN.match(geom):
            return "ST_GeomFromText(%s, 4326)", [geom]

        parts = [p for p in re.split(r"[,\s;]+", geom) if p]
        if len(parts) >= 2:
            lat = _safe_float(parts[0])
            lon = _safe_float(parts[1])
            if lat is not None and lon is not None:
                return "ST_SetSRID(ST_MakePoint(%s, %s), 4326)", [lon, lat]

    latitude_keys = ("latitude", "lat", "gps_point_latitude", "_gps_point_latitude", "gps_latitude", "y")
    longitude_keys = ("longitude", "lon", "lng", "gps_point_longitude", "_gps_point_longitude", "gps_longitude", "x")
    lat = next((_safe_float(row.get(key)) for key in latitude_keys if _safe_float(row.get(key)) is not None), None)
    lon = next((_safe_float(row.get(key)) for key in longitude_keys if _safe_float(row.get(key)) is not None), None)

    if lat is not None and lon is not None:
        return "ST_SetSRID(ST_MakePoint(%s, %s), 4326)", [lon, lat]

    return None


def find_potential_existing_identifiers(
    dataset: DatasetDefinition,
    stage_columns: list[StageColumn],
    records: list[dict[str, str]],
    project_code: str,
) -> set[str]:
    if not records:
        return set()

    stage_column_names = {col.name for col in stage_columns}
    identifier = next((field for field in dataset.identifier_fields if field in stage_column_names), None)
    if not identifier:
        return set()

    values: list[str] = []
    for row in records:
        current = str(row.get(identifier, "")).strip()
        if current:
            values.append(current)
    values = list(dict.fromkeys(values))[:500]
    if not values:
        return set()

    conditions = [f"{_quote_ident(identifier)} = ANY(%s)"]
    params: list[Any] = [values]
    if "project_code" in stage_column_names:
        conditions.append(f"{_quote_ident('project_code')} = %s")
        params.append(project_code)

    sql = f"""
        SELECT {_quote_ident(identifier)}
        FROM stage.{_quote_ident(dataset.stage_table)}
        WHERE {" AND ".join(conditions)}
    """

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
        return {str(row[0]).strip() for row in rows if row and row[0]}
    except Exception:
        return set()


def build_validation_report(
    parse_result: CsvParseResult,
    dataset: DatasetDefinition,
    stage_columns: list[StageColumn],
    project_code: str,
    region_id: str | None,
    existing_identifiers: set[str] | None = None,
) -> dict[str, Any]:
    if existing_identifiers is None:
        existing_identifiers = set()

    stage_column_names = {col.name for col in stage_columns}
    recognized = [h for h in parse_result.headers_normalized if h in stage_column_names]
    unknown = [h for h in parse_result.headers_normalized if h not in stage_column_names]

    warnings: list[str] = []
    errors: list[str] = []

    if parse_result.row_count == 0:
        errors.append("Le CSV ne contient aucune ligne de donnees.")

    if not recognized:
        errors.append("Aucune colonne CSV ne correspond aux colonnes de la table stage cible.")

    identifier = next((f for f in dataset.identifier_fields if f in parse_result.headers_normalized), None)
    duplicate_identifiers: list[str] = []
    # Classify rows into new / existing / duplicate
    new_rows_list: list[dict[str, str]] = []
    existing_rows_list: list[dict[str, str]] = []
    duplicate_rows_list: list[dict[str, str]] = []
    new_count = 0
    existing_count = 0
    duplicate_count = 0

    if identifier:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for row in parse_result.records:
            value = str(row.get(identifier, "")).strip()
            if not value:
                # Rows without identifier value count as new
                new_count += 1
                if len(new_rows_list) < 50:
                    new_rows_list.append(row)
                continue
            if value in seen:
                # Duplicate within the file (2nd+ occurrence)
                duplicates.add(value)
                duplicate_count += 1
                if len(duplicate_rows_list) < 50:
                    duplicate_rows_list.append(row)
            elif value in existing_identifiers:
                existing_count += 1
                if len(existing_rows_list) < 50:
                    existing_rows_list.append(row)
            else:
                new_count += 1
                if len(new_rows_list) < 50:
                    new_rows_list.append(row)
            seen.add(value)
        duplicate_identifiers = sorted(duplicates)
        if duplicate_identifiers:
            warnings.append(
                f"Doublons detectes sur '{identifier}' dans le fichier: {len(duplicate_identifiers)} valeur(s)."
            )
    else:
        warnings.append("Aucune colonne identifiant connue detectee pour ce dataset.")
        # Without identifier, all rows are considered new
        new_count = parse_result.row_count
        new_rows_list = parse_result.records[:50]

    invalid_geom_rows = 0
    if "geom" in stage_column_names:
        for row in parse_result.records:
            raw_geom = row.get("geom")
            has_any_geo_hint = bool(
                str(raw_geom or "").strip()
                or str(row.get("gps_point") or "").strip()
                or str(row.get("grp_loc_gps_point") or "").strip()
                or str(row.get("latitude") or "").strip()
                or str(row.get("longitude") or "").strip()
                or str(row.get("_gps_point_latitude") or "").strip()
                or str(row.get("_gps_point_longitude") or "").strip()
                or str(row.get("gps_point_latitude") or "").strip()
                or str(row.get("gps_point_longitude") or "").strip()
            )
            if has_any_geo_hint and build_geom_sql(raw_geom, row) is None:
                invalid_geom_rows += 1

    if invalid_geom_rows:
        warnings.append(f"Geometrie non interpretable sur {invalid_geom_rows} ligne(s).")

    if unknown:
        warnings.append(f"{len(unknown)} colonne(s) non reconnue(s) seront ignorees.")

    potential_existing_count = len(existing_identifiers)
    if potential_existing_count:
        warnings.append(f"{potential_existing_count} enregistrement(s) semblent deja presents en base stage.")

    # Build detailed column_mapping
    csv_header_set = set(parse_result.headers_normalized)
    column_mapping: list[dict[str, Any]] = []

    # Matched columns: CSV header exists in stage schema
    for header in parse_result.headers_normalized:
        if header in stage_column_names:
            column_mapping.append({
                "csv_header": header,
                "stage_column": header,
                "status": "matched",
                "category": categorize_column(header),
            })
        else:
            column_mapping.append({
                "csv_header": header,
                "stage_column": None,
                "status": "unmatched",
                "category": None,
            })

    # Auto-filled columns: stage columns not in CSV but injected by backend
    auto_filled_columns = {
        "project_code", "import_batch", "import_source", "imported_at", "raw_payload",
    }
    for col in stage_columns:
        if col.name not in csv_header_set and col.name in auto_filled_columns:
            column_mapping.append({
                "csv_header": None,
                "stage_column": col.name,
                "status": "auto_filled",
                "category": categorize_column(col.name),
            })

    return {
        "valid": not errors,
        "dataset": {
            "code": dataset.code,
            "label": dataset.label,
            "stage_table": f"stage.{dataset.stage_table}",
            "project_code": project_code,
            "region_id": region_id,
        },
        "stats": {
            "rows_total": parse_result.row_count,
            "columns_total": len(parse_result.headers_normalized),
            "columns_recognized": len(recognized),
            "columns_unknown": len(unknown),
            "duplicate_identifier_count": len(duplicate_identifiers),
            "potential_existing_count": potential_existing_count,
            "invalid_geom_rows": invalid_geom_rows,
            "new_count": new_count,
            "existing_count": existing_count,
            "duplicate_count": duplicate_count,
        },
        "columns": {
            "expected": [col.name for col in stage_columns],
            "recognized": recognized,
            "unknown": unknown,
        },
        "column_mapping": column_mapping,
        "identifier_field": identifier,
        "new_rows": new_rows_list,
        "existing_rows": existing_rows_list,
        "duplicate_rows": duplicate_rows_list,
        "warnings": warnings,
        "errors": errors,
    }


def execute_import_into_stage(
    dataset: DatasetDefinition,
    parse_result: CsvParseResult,
    project_code: str,
    region_id: str | None,
    import_batch: uuid.UUID,
    import_source: str = "admin_csv",
    on_duplicate: str = "update",
) -> dict[str, Any]:
    stage_columns = get_stage_columns(dataset.stage_table)
    pk_columns = get_stage_primary_key_columns(dataset.stage_table)
    pk_column_set = set(pk_columns)
    stage_column_names = {col.name for col in stage_columns}

    records = parse_result.records

    if on_duplicate == "update_only":
        # Filter records to keep only those whose identifier exists in stage
        existing_ids = find_potential_existing_identifiers(
            dataset=dataset,
            stage_columns=stage_columns,
            records=records,
            project_code=project_code,
        )
        identifier = next(
            (field for field in dataset.identifier_fields if field in stage_column_names),
            None,
        )
        if identifier and existing_ids:
            records = [
                row for row in records
                if str(row.get(identifier, "")).strip() in existing_ids
            ]
        else:
            records = []
        # update_only uses the same SQL as "update" (ON CONFLICT DO UPDATE)
        on_duplicate = "update"

    rows_total = len(records)
    rows_ok = 0
    row_errors: list[dict[str, Any]] = []

    insert_sql_template_prefix = f"INSERT INTO stage.{_quote_ident(dataset.stage_table)}"

    with connection.cursor() as cursor:
        for row_index, raw_row in enumerate(records, start=1):
            try:
                columns: list[str] = []
                values_sql: list[str] = []
                params: list[Any] = []

                for col in stage_columns:
                    col_name = col.name

                    has_row_value = col_name in raw_row and str(raw_row.get(col_name, "")).strip() != ""
                    value: Any = raw_row.get(col_name) if has_row_value else None

                    if col_name == "project_code":
                        value = project_code
                        has_row_value = True
                    elif col_name == "region" and region_id and not has_row_value:
                        value = region_id
                        has_row_value = True
                    elif col_name == "raw_uuid":
                        if not has_row_value:
                            # Kobo exports typically provide `_uuid` (normalized to `uuid`).
                            for candidate in ("raw_uuid", "uuid", "_uuid", "meta_instanceid", "meta_instance_id"):
                                candidate_value = str(raw_row.get(candidate, "")).strip()
                                if candidate_value:
                                    value = candidate_value
                                    has_row_value = True
                                    break
                        if not has_row_value:
                            value = str(uuid.uuid4())
                        has_row_value = True
                    elif col_name == "submission_uuid":
                        if not has_row_value:
                            # Kobo exports typically provide `_uuid` (normalized to `uuid`).
                            for candidate in ("submission_uuid", "uuid", "_uuid", "raw_uuid"):
                                candidate_value = str(raw_row.get(candidate, "")).strip()
                                if candidate_value:
                                    value = candidate_value
                                    has_row_value = True
                                    break
                    elif col_name == "enumerator_id":
                        if not has_row_value:
                            for candidate in ("enumerator_id", "username", "submitted_by", "_submitted_by"):
                                candidate_value = str(raw_row.get(candidate, "")).strip()
                                if candidate_value:
                                    value = candidate_value
                                    has_row_value = True
                                    break
                    elif col_name == "id_commune":
                        if not has_row_value:
                            # Kobo exports typically provide `commune` (or `grp_loc/commune` -> `grp_loc_commune`).
                            for candidate in ("id_commune", "commune", "grp_loc_commune"):
                                candidate_value = str(raw_row.get(candidate, "")).strip()
                                if candidate_value:
                                    value = candidate_value
                                    has_row_value = True
                                    break
                    elif col_name == "import_source":
                        value = import_source
                        has_row_value = True
                    elif col_name == "import_batch":
                        value = str(import_batch)
                        has_row_value = True
                    elif col_name == "imported_at":
                        value = timezone.now()
                        has_row_value = True
                    elif col_name == "raw_payload":
                        value = json.dumps(raw_row, ensure_ascii=False)
                        has_row_value = True

                    if col.udt_name == "geometry":
                        # Geometry columns are stored as PostGIS geometries.
                        geom_sql: tuple[str, list[Any]] | None = None

                        # 1) Dataset-specific handling for Kobo geoshape/geotrace strings.
                        if col_name == "geom_zone":
                            shape_raw = str(value or "").strip() or str(raw_row.get("zone_geom") or "").strip()
                            wkt = _build_polygon_wkt(shape_raw) if shape_raw else None
                            if wkt:
                                geom_sql = ("ST_GeomFromText(%s, 4326)", [wkt])
                        elif col_name == "geom":
                            trace_raw = str(raw_row.get("trace_couloir") or "").strip()
                            wkt = _build_linestring_wkt(trace_raw) if trace_raw else None
                            if wkt:
                                geom_sql = ("ST_GeomFromText(%s, 4326)", [wkt])

                        # 2) Generic handling (WKT / EWKT / gps_point / lat+lon).
                        if geom_sql is None:
                            geom_sql = build_geom_sql(str(value or ""), raw_row)

                        if geom_sql is None:
                            continue

                        expression, geom_params = geom_sql
                        columns.append(_quote_ident(col_name))
                        values_sql.append(expression)
                        params.extend(geom_params)
                        continue

                    if not has_row_value:
                        continue

                    coerced = coerce_value(value, col)
                    if coerced is None:
                        if not col.is_nullable and not col.has_default:
                            raise ValueError(f"colonne '{col_name}' obligatoire.")
                        continue

                    columns.append(_quote_ident(col_name))
                    values_sql.append("%s")
                    params.append(coerced)

                if not columns:
                    raise ValueError("aucune colonne exploitable.")

                sql = f"{insert_sql_template_prefix} ({', '.join(columns)}) VALUES ({', '.join(values_sql)})"

                if pk_columns:
                    conflict_target = ", ".join(_quote_ident(name) for name in pk_columns)
                    if on_duplicate == "skip":
                        sql += f" ON CONFLICT ({conflict_target}) DO NOTHING"
                    else:
                        # Generic idempotency: update the inserted columns (except PK) on conflict.
                        inserted_names = [c.strip('"') for c in columns]
                        assignments = [
                            f"{_quote_ident(name)} = EXCLUDED.{_quote_ident(name)}"
                            for name in inserted_names
                            if name not in pk_column_set
                        ]
                        if assignments:
                            sql += f" ON CONFLICT ({conflict_target}) DO UPDATE SET {', '.join(assignments)}"
                        else:
                            sql += f" ON CONFLICT ({conflict_target}) DO NOTHING"

                cursor.execute(sql, params)
                rows_ok += 1
            except Exception as exc:
                row_errors.append(
                    {
                        "row_number": row_index,
                        "message": str(exc),
                    }
                )

    rows_error = rows_total - rows_ok
    return {
        "stage_table": f"stage.{dataset.stage_table}",
        "rows_total": rows_total,
        "rows_ok": rows_ok,
        "rows_error": rows_error,
        "errors": row_errors[:200],
    }
