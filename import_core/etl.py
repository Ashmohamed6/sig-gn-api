from __future__ import annotations

import hashlib
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
        identifier_fields=("id_intrant", "raw_uuid"),
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
        identifier_fields=("raw_uuid", "domaine_emploi"),
    ),
    DatasetDefinition(
        code="fiere-insertion-domaines",
        label="Domaines insertion",
        stage_table="fiere_insertion_dom_raw",
        project_codes=("FIERE",),
        aliases=("fiere-insertion-dom",),
        identifier_fields=("raw_uuid", "domaine_insertion"),
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
    "meta_rootuuid",
})

_PAYLOAD_ONLY_DATA_COLUMNS = frozenset({
    # These fields are present in some Kobo exports but have no dedicated stage column.
    # They are preserved inside raw_payload.
    "couloir_present",
    "photo_station",
    "photo_station_url",
    "photo_source",
    "photo_source_url",
    "photo_restaur",
    "photo_restaur_url",
})


def categorize_column(column_name: str) -> str:
    if column_name in _SYSTEM_COLUMNS:
        return "system"
    if column_name in _KOBO_META_COLUMNS:
        return "kobo_meta"
    return "data"


_CHECKBOX_TRUE_VALUES = frozenset({"1", "true", "t", "yes", "y", "oui", "vrai", "x"})
_CHECKBOX_FALSE_VALUES = frozenset({"0", "false", "f", "no", "n", "non", "faux"})
_AUTO_IDENTIFIER_EXPLICIT_FIELDS = frozenset({"code_station", "code_ouvrage"})
_AUTO_IDENTIFIER_SEED_FIELDS = (
    "raw_uuid",
    "uuid",
    "meta_rootuuid",
    "submission_uuid",
    "id",
    "index",
    "submission_time",
    "start",
    "today",
    "deviceid",
    "username",
    "nom_comite",
    "commune",
    "localite",
    "id_ent",
    "nom_acteur",
    "id_couloir",
    "id_org",
    "id_menage",
)


def _parse_checkbox_token(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in _CHECKBOX_TRUE_VALUES:
        return True
    if text in _CHECKBOX_FALSE_VALUES:
        return False
    return None


def _normalize_identifier_seed(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower().startswith("uuid:"):
        text = text[5:].strip()
    return text


def _is_auto_identifier_field(field_name: str) -> bool:
    name = str(field_name or "").strip().lower()
    if not name:
        return False
    if name in _AUTO_IDENTIFIER_EXPLICIT_FIELDS:
        return True
    return (
        name == "id"
        or name.startswith("id_")
        or name.endswith("_id")
        or "uuid" in name
    )


def _generate_auto_identifier(
    field_name: str,
    column: StageColumn,
    row: dict[str, str],
    row_number: int,
    *,
    suffix: int = 0,
) -> str:
    seed_parts: list[str] = []
    seed_parts.append(f"field={field_name}")
    for field in _AUTO_IDENTIFIER_SEED_FIELDS:
        value = _normalize_identifier_seed(row.get(field))
        if value:
            seed_parts.append(f"{field}={value}")

    if len(seed_parts) == 1:
        # Last-resort deterministic seed within the file payload.
        seed_parts.append(f"row_number={row_number}")
    if suffix:
        seed_parts.append(f"suffix={suffix}")

    seed_text = "|".join(seed_parts)
    if column.udt_name == "uuid":
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed_text))

    digest = hashlib.sha1(seed_text.encode("utf-8")).hexdigest()
    if column.udt_name in {"int2", "int4", "int8"}:
        # Keep positive deterministic integers for numeric identifiers.
        if column.udt_name == "int2":
            max_value = 32767
        elif column.udt_name == "int4":
            max_value = 2147483647
        else:
            max_value = 9223372036854775807
        value = (int(digest[:16], 16) % (max_value - 1)) + 1
        return str(value)

    if field_name == "id_comite":
        prefix = "AUTO-COMITE"
    else:
        cleaned = re.sub(r"[^A-Z0-9]+", "-", field_name.upper()).strip("-")
        prefix = f"AUTO-{cleaned or 'ID'}"
    return f"{prefix}-{digest[:12].upper()}"


def _split_codes(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = [item.strip().upper() for item in re.split(r"[\s,|;]+", text) if item.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _build_stage_alias_lookup(stage_column_names: set[str]) -> dict[str, str]:
    alias_to_target: dict[str, str] = {}

    def register(alias_name: str, target_name: str) -> None:
        alias = str(alias_name or "").strip()
        target = str(target_name or "").strip()
        if not alias or not target:
            return
        if target not in stage_column_names:
            return
        alias_to_target.setdefault(alias, target)

    for col in sorted(stage_column_names):
        register(col, col)
        register(f"{col}_url", col)

        # Singular/plural drift observed in Kobo forms (e.g. theme_comite vs themes_comite).
        if col.endswith("s") and len(col) > 1:
            register(col[:-1], col)

        if col.startswith("types_"):
            register(f"type_{col[6:]}", col)
        if col.startswith("themes_"):
            register(f"theme_{col[7:]}", col)

    if "region" in stage_column_names:
        register("grp_loc_region", "region")
    if "prefecture" in stage_column_names:
        register("grp_loc_prefecture", "prefecture")
    if "localite" in stage_column_names:
        register("grp_loc_localite", "localite")

    if "commune" in stage_column_names:
        register("grp_loc_commune", "commune")
        register("id_commune", "commune")
    if "id_commune" in stage_column_names:
        register("commune", "id_commune")
        register("grp_loc_commune", "id_commune")

    if "code_station" in stage_column_names:
        register("station_id", "code_station")

    if "geom" in stage_column_names:
        register("trace_couloir", "geom")
    if "geom_zone" in stage_column_names:
        register("zone_geom", "geom_zone")

    return alias_to_target


def _infer_checkbox_derivations(
    records: list[dict[str, str]],
    stage_column_names: set[str],
    alias_to_target: dict[str, str],
) -> dict[str, tuple[str, str]]:
    if not records:
        return {}

    headers: set[str] = set()
    for row in records:
        headers.update(row.keys())

    # Longest prefixes first to avoid ambiguous matches.
    candidate_prefixes = sorted(
        {name for name, target in alias_to_target.items() if target in stage_column_names}
        | stage_column_names,
        key=len,
        reverse=True,
    )

    out: dict[str, tuple[str, str]] = {}
    for header in headers:
        if header in stage_column_names:
            continue

        target_name = ""
        source_prefix = ""
        for prefix in candidate_prefixes:
            if not header.startswith(prefix + "_"):
                continue
            target = alias_to_target.get(prefix, prefix)
            if target not in stage_column_names:
                continue
            target_name = target
            source_prefix = prefix
            break

        if not target_name or not source_prefix:
            continue

        non_empty_values = [str(row.get(header, "")).strip() for row in records if str(row.get(header, "")).strip()]
        # Restrict derivation to checkbox-like fields (0/1, yes/no, true/false)
        # when values are present. Fully empty columns are still accepted.
        if non_empty_values and any(_parse_checkbox_token(value) is None for value in non_empty_values):
            continue

        out[header] = (target_name, source_prefix)

    return out


def prepare_records_for_stage(
    records: list[dict[str, str]],
    stage_columns: list[StageColumn],
    identifier_fields: tuple[str, ...] | None = None,
) -> tuple[list[dict[str, str]], dict[str, str], dict[str, tuple[str, str]]]:
    stage_column_names = {col.name for col in stage_columns}
    stage_columns_by_name = {col.name: col for col in stage_columns}
    alias_to_target = _build_stage_alias_lookup(stage_column_names)
    alias_header_map = {
        header: target
        for header, target in alias_to_target.items()
        if header not in stage_column_names and target in stage_column_names
    }
    checkbox_header_map = _infer_checkbox_derivations(records, stage_column_names, alias_to_target)

    auto_identifier_fields = tuple(
        field
        for field in (identifier_fields or ())
        if field in stage_column_names and _is_auto_identifier_field(field)
    )
    known_identifier_values: dict[str, set[str]] = {field: set() for field in auto_identifier_fields}
    generated_identifier_values: dict[str, set[str]] = {field: set() for field in auto_identifier_fields}
    for raw_row in records:
        for field in auto_identifier_fields:
            current_value = str(raw_row.get(field, "")).strip()
            if current_value:
                known_identifier_values[field].add(current_value)

    prepared_records: list[dict[str, str]] = []
    for row_number, raw_row in enumerate(records, start=1):
        row = dict(raw_row)

        # Copy values from known aliases only when canonical target is empty.
        for source_header, target_header in alias_header_map.items():
            source_value = str(row.get(source_header, "")).strip()
            if not source_value:
                continue
            if str(row.get(target_header, "")).strip():
                continue
            row[target_header] = source_value

        # File attachments often expose a companion *_url field; keep URL when main field is empty.
        for target_header in stage_column_names:
            url_header = f"{target_header}_url"
            if url_header not in row:
                continue
            target_value = str(row.get(target_header, "")).strip()
            url_value = str(row.get(url_header, "")).strip()
            if not target_value and url_value:
                row[target_header] = url_value

        selected_codes_by_target: dict[str, list[str]] = {}
        for source_header, (target_header, source_prefix) in checkbox_header_map.items():
            token = _parse_checkbox_token(row.get(source_header))
            if token is not True:
                continue

            suffix = source_header[len(source_prefix) + 1 :].strip()
            if not suffix:
                continue
            selected_codes_by_target.setdefault(target_header, []).append(suffix.upper())

        for target_header, selected_codes in selected_codes_by_target.items():
            merged_codes = _split_codes(row.get(target_header))
            for code in selected_codes:
                if code not in merged_codes:
                    merged_codes.append(code)
            if merged_codes:
                row[target_header] = " ".join(merged_codes)

        # Kobo forms can produce empty identifiers; generate stable values for all
        # dataset identifier fields that are id/uuid-like.
        for field in auto_identifier_fields:
            current_value = str(row.get(field, "")).strip()
            if current_value:
                known_identifier_values[field].add(current_value)
                continue

            column = stage_columns_by_name[field]
            candidate_value = _generate_auto_identifier(field, column, row, row_number, suffix=0)
            if (
                candidate_value in known_identifier_values[field]
                and candidate_value not in generated_identifier_values[field]
            ):
                suffix = 2
                while True:
                    candidate_value = _generate_auto_identifier(field, column, row, row_number, suffix=suffix)
                    if (
                        candidate_value not in known_identifier_values[field]
                        or candidate_value in generated_identifier_values[field]
                    ):
                        break
                    suffix += 1

            row[field] = candidate_value
            known_identifier_values[field].add(candidate_value)
            generated_identifier_values[field].add(candidate_value)

        prepared_records.append(row)

    return prepared_records, alias_header_map, checkbox_header_map


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


def _build_polygon_geom_sql(raw_geom: str | None, row: dict[str, str]) -> tuple[str, list[Any]] | None:
    candidates: list[str] = []
    raw_text = str(raw_geom or "").strip()
    if raw_text:
        candidates.append(raw_text)

    for key in ("zone_geom", "geoshape"):
        value = str(row.get(key) or "").strip()
        if value:
            candidates.append(value)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        upper = candidate.upper()
        if upper.startswith("SRID="):
            if "POLYGON" in upper:
                return "ST_GeomFromEWKT(%s)", [candidate]
            continue

        if upper.startswith("POLYGON(") or upper.startswith("MULTIPOLYGON("):
            return "ST_GeomFromText(%s, 4326)", [candidate]

        wkt = _build_polygon_wkt(candidate)
        if wkt:
            return "ST_GeomFromText(%s, 4326)", [wkt]

    return None


def _build_linestring_geom_sql(raw_geom: str | None, row: dict[str, str]) -> tuple[str, list[Any]] | None:
    candidates: list[str] = []
    raw_text = str(raw_geom or "").strip()
    if raw_text:
        candidates.append(raw_text)

    for key in ("trace_couloir", "trace", "geotrace"):
        value = str(row.get(key) or "").strip()
        if value:
            candidates.append(value)

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)

        upper = candidate.upper()
        if upper.startswith("SRID="):
            if "LINESTRING" in upper:
                return "ST_GeomFromEWKT(%s)", [candidate]
            continue

        if upper.startswith("LINESTRING(") or upper.startswith("MULTILINESTRING("):
            return "ST_GeomFromText(%s, 4326)", [candidate]

        wkt = _build_linestring_wkt(candidate)
        if wkt:
            return "ST_GeomFromText(%s, 4326)", [wkt]

    return None


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
    prepared_records, alias_header_map, checkbox_header_map = prepare_records_for_stage(
        parse_result.records,
        stage_columns,
        identifier_fields=getattr(dataset, "identifier_fields", ()),
    )

    direct_headers = [h for h in parse_result.headers_normalized if h in stage_column_names]
    alias_headers = [h for h in parse_result.headers_normalized if h in alias_header_map]
    checkbox_headers = [h for h in parse_result.headers_normalized if h in checkbox_header_map]
    ignored_kobo_meta_headers = [
        h for h in parse_result.headers_normalized
        if h in _KOBO_META_COLUMNS
        and h not in stage_column_names
        and h not in alias_header_map
        and h not in checkbox_header_map
    ]
    payload_only_headers = [
        h for h in parse_result.headers_normalized
        if h in _PAYLOAD_ONLY_DATA_COLUMNS
        and h not in stage_column_names
        and h not in alias_header_map
        and h not in checkbox_header_map
    ]
    unknown = [
        h for h in parse_result.headers_normalized
        if h not in stage_column_names
        and h not in alias_header_map
        and h not in checkbox_header_map
        and h not in _KOBO_META_COLUMNS
        and h not in _PAYLOAD_ONLY_DATA_COLUMNS
    ]

    warnings: list[str] = []
    errors: list[str] = []

    if parse_result.row_count == 0:
        errors.append("Le CSV ne contient aucune ligne de donnees.")

    if not direct_headers and not alias_headers and not checkbox_headers:
        errors.append("Aucune colonne CSV ne correspond aux colonnes de la table stage cible.")

    identifier = next(
        (
            field
            for field in dataset.identifier_fields
            if field in stage_column_names and any(str(row.get(field, "")).strip() for row in prepared_records)
        ),
        None,
    )
    if identifier is None:
        identifier = next(
            (
                field
                for field in dataset.identifier_fields
                if any(str(row.get(field, "")).strip() for row in prepared_records)
            ),
            None,
        )
    duplicate_identifiers: list[str] = []
    # Classify rows into new / existing / duplicate
    new_rows_list: list[dict[str, str]] = []
    existing_rows_list: list[dict[str, str]] = []
    duplicate_rows_list: list[dict[str, str]] = []
    missing_identifier_rows_list: list[dict[str, str]] = []
    new_count = 0
    existing_count = 0
    duplicate_count = 0
    missing_identifier_count = 0
    auto_generated_identifier_count = 0

    if identifier:
        for raw_row, prepared_row in zip(parse_result.records, prepared_records):
            raw_value = str(raw_row.get(identifier, "")).strip()
            prepared_value = str(prepared_row.get(identifier, "")).strip()
            if not raw_value and prepared_value:
                auto_generated_identifier_count += 1

        seen: set[str] = set()
        duplicates: set[str] = set()
        for row in prepared_records:
            value = str(row.get(identifier, "")).strip()
            if not value:
                missing_identifier_count += 1
                if len(missing_identifier_rows_list) < 50:
                    missing_identifier_rows_list.append(row)
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
        if missing_identifier_count:
            warnings.append(
                f"{missing_identifier_count} ligne(s) sans '{identifier}' ne seront pas comptabilisees comme nouvelles."
            )
        if auto_generated_identifier_count:
            warnings.append(
                f"{auto_generated_identifier_count} ligne(s) sans '{identifier}' ont recu un identifiant auto-genere."
            )
    else:
        warnings.append("Aucune colonne identifiant connue detectee pour ce dataset.")
        # Without identifier, all rows are considered new
        new_count = parse_result.row_count
        new_rows_list = prepared_records[:50]

    invalid_geom_rows = 0
    if "geom" in stage_column_names:
        for row in prepared_records:
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
            if dataset.code == "agr-couloirs":
                geom_sql = _build_linestring_geom_sql(raw_geom, row)
            elif dataset.code == "agr-zones-degradees" and "geom_zone" in stage_column_names:
                geom_sql = _build_polygon_geom_sql(row.get("geom_zone"), row)
            else:
                geom_sql = build_geom_sql(raw_geom, row)
            if has_any_geo_hint and geom_sql is None:
                invalid_geom_rows += 1

    if invalid_geom_rows:
        warnings.append(f"Geometrie non interpretable sur {invalid_geom_rows} ligne(s).")

    if checkbox_headers:
        mapped_targets = sorted({checkbox_header_map[h][0] for h in checkbox_headers})
        warnings.append(
            f"{len(checkbox_headers)} colonne(s) checkbox Kobo mappees automatiquement vers {len(mapped_targets)} champ(s)."
        )

    if ignored_kobo_meta_headers:
        warnings.append(
            f"{len(ignored_kobo_meta_headers)} metadonnee(s) Kobo seront conservees dans raw_payload."
        )

    if payload_only_headers:
        warnings.append(
            f"{len(payload_only_headers)} colonne(s) metier sans colonne stage seront conservees dans raw_payload."
        )

    if unknown:
        warnings.append(f"{len(unknown)} colonne(s) non reconnue(s) seront ignorees.")

    potential_existing_count = len(existing_identifiers)
    if potential_existing_count:
        warnings.append(f"{potential_existing_count} enregistrement(s) semblent deja presents en base stage.")

    # Build detailed column_mapping
    csv_header_set = set(parse_result.headers_normalized)
    column_mapping: list[dict[str, Any]] = []

    recognized_stage_columns: list[str] = []
    seen_stage_columns: set[str] = set()

    for header in parse_result.headers_normalized:
        if header in stage_column_names:
            if header not in seen_stage_columns:
                recognized_stage_columns.append(header)
                seen_stage_columns.add(header)
            column_mapping.append({
                "csv_header": header,
                "stage_column": header,
                "status": "matched",
                "category": categorize_column(header),
            })
        elif header in alias_header_map:
            target = alias_header_map[header]
            if target not in seen_stage_columns:
                recognized_stage_columns.append(target)
                seen_stage_columns.add(target)
            column_mapping.append({
                "csv_header": header,
                "stage_column": target,
                "status": "alias_mapped",
                "category": categorize_column(target),
            })
        elif header in checkbox_header_map:
            target = checkbox_header_map[header][0]
            if target not in seen_stage_columns:
                recognized_stage_columns.append(target)
                seen_stage_columns.add(target)
            column_mapping.append({
                "csv_header": header,
                "stage_column": target,
                "status": "derived_checkbox",
                "category": categorize_column(target),
            })
        elif header in _KOBO_META_COLUMNS:
            column_mapping.append({
                "csv_header": header,
                "stage_column": None,
                "status": "ignored_kobo_meta",
                "category": "kobo_meta",
            })
        elif header in _PAYLOAD_ONLY_DATA_COLUMNS:
            column_mapping.append({
                "csv_header": header,
                "stage_column": None,
                "status": "ignored_payload_only",
                "category": "data",
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
            "columns_recognized": len(direct_headers) + len(alias_headers) + len(checkbox_headers),
            "columns_unknown": len(unknown),
            "duplicate_identifier_count": len(duplicate_identifiers),
            "potential_existing_count": potential_existing_count,
            "invalid_geom_rows": invalid_geom_rows,
            "new_count": new_count,
            "existing_count": existing_count,
            "duplicate_count": duplicate_count,
            "missing_identifier_count": missing_identifier_count,
            "auto_generated_identifier_count": auto_generated_identifier_count,
        },
        "columns": {
            "expected": [col.name for col in stage_columns],
            "recognized": recognized_stage_columns,
            "unknown": unknown,
        },
        "column_mapping": column_mapping,
        "identifier_field": identifier,
        "new_rows": new_rows_list,
        "existing_rows": existing_rows_list,
        "duplicate_rows": duplicate_rows_list,
        "missing_identifier_rows": missing_identifier_rows_list,
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

    records, _, _ = prepare_records_for_stage(
        parse_result.records,
        stage_columns,
        identifier_fields=getattr(dataset, "identifier_fields", ()),
    )

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
    elif on_duplicate == "skip":
        # "Skip" mode is used by the UI action "Importer nouvelles lignes":
        # keep only non-existing identifiers (and first occurrence in file).
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
        if identifier:
            seen_identifiers: set[str] = set()
            filtered_records: list[dict[str, str]] = []
            for row in records:
                value = str(row.get(identifier, "")).strip()
                if not value:
                    continue
                if value in seen_identifiers:
                    continue
                seen_identifiers.add(value)
                if value in existing_ids:
                    continue
                filtered_records.append(row)
            records = filtered_records
            # Keep "new lines only" semantics, but still upsert on PK conflicts
            # (ex: same raw_uuid already staged with empty identifier).
            on_duplicate = "update"

    rows_total = len(records)
    rows_ok = 0
    rows_skipped = 0
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
                            for candidate in (
                                "raw_uuid",
                                "uuid",
                                "_uuid",
                                "meta_rootuuid",
                                "meta_instanceid",
                                "meta_instance_id",
                            ):
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
                            geom_sql = _build_polygon_geom_sql(value, raw_row)
                        elif col_name == "geom":
                            if dataset.code == "agr-couloirs":
                                # Couloirs require LINESTRING geometry.
                                geom_sql = _build_linestring_geom_sql(value, raw_row)
                            else:
                                trace_raw = str(raw_row.get("trace_couloir") or "").strip()
                                wkt = _build_linestring_wkt(trace_raw) if trace_raw else None
                                if wkt:
                                    geom_sql = ("ST_GeomFromText(%s, 4326)", [wkt])

                        # 2) Generic handling (WKT / EWKT / gps_point / lat+lon).
                        # For couloirs/zones, avoid generic POINT fallback on line/polygon targets.
                        if geom_sql is None and not (
                            (dataset.code == "agr-couloirs" and col_name == "geom")
                            or (dataset.code == "agr-zones-degradees" and col_name == "geom_zone")
                        ):
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
                if int(cursor.rowcount or 0) == 0:
                    rows_skipped += 1
                else:
                    rows_ok += 1
            except Exception as exc:
                row_errors.append(
                    {
                        "row_number": row_index,
                        "message": str(exc),
                    }
                )

    rows_error = len(row_errors)
    return {
        "stage_table": f"stage.{dataset.stage_table}",
        "rows_total": rows_total,
        "rows_ok": rows_ok,
        "rows_skipped": rows_skipped,
        "rows_error": rows_error,
        "errors": row_errors[:200],
    }
