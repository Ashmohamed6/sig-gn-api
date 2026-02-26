import csv
import io
import re
import unicodedata
from dataclasses import dataclass


MAX_CSV_SIZE_BYTES = 10 * 1024 * 1024
_HEADER_SANITIZE_PATTERN = re.compile(r"[^a-z0-9_]+")
_HEADER_DUPLICATE_SEPARATOR = "__dup_"


class CsvParserError(ValueError):
    """Erreur fonctionnelle de parsing CSV."""


@dataclass(frozen=True)
class CsvParseResult:
    delimiter: str
    headers_raw: list[str]
    headers_normalized: list[str]
    records: list[dict[str, str]]
    row_count: int


def normalize_header_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower().replace("/", "_").replace("-", "_").replace(" ", "_")
    text = _HEADER_SANITIZE_PATTERN.sub("_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _deduplicate_headers(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    resolved: list[str] = []

    for idx, raw in enumerate(headers, start=1):
        normalized = normalize_header_name(raw) or f"col_{idx}"
        count = seen.get(normalized, 0)
        seen[normalized] = count + 1
        if count:
            resolved.append(f"{normalized}{_HEADER_DUPLICATE_SEPARATOR}{count + 1}")
        else:
            resolved.append(normalized)

    return resolved


def _decode_utf8_bytes(payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvParserError("Fichier CSV invalide: encodage UTF-8 requis.") from exc


def parse_csv_upload(uploaded_file, *, max_size_bytes: int = MAX_CSV_SIZE_BYTES) -> CsvParseResult:
    if uploaded_file is None:
        raise CsvParserError("Aucun fichier CSV recu.")

    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise CsvParserError("Le fichier CSV est vide.")

    if size > max_size_bytes:
        raise CsvParserError("Fichier trop volumineux: limite 10 MB.")

    payload = uploaded_file.read()
    if not payload:
        raise CsvParserError("Le fichier CSV est vide.")

    text = _decode_utf8_bytes(payload)
    lines = text.splitlines()
    if not lines:
        raise CsvParserError("Le fichier CSV est vide.")

    first_line = lines[0]
    if ";" not in first_line:
        if "," in first_line:
            raise CsvParserError("Separateur invalide: utilisez ';' (point-virgule).")
        raise CsvParserError("Format CSV invalide: separateur ';' non detecte.")

    reader = csv.DictReader(io.StringIO(text), delimiter=";", quotechar='"')
    if not reader.fieldnames:
        raise CsvParserError("Entete CSV introuvable.")

    headers_raw = [str(h or "").strip() for h in reader.fieldnames]
    headers_normalized = _deduplicate_headers(headers_raw)

    records: list[dict[str, str]] = []
    for row in reader:
        normalized_row: dict[str, str] = {}
        is_blank = True

        for idx, key in enumerate(headers_raw):
            normalized_key = headers_normalized[idx]
            value = row.get(key)
            text_value = str(value).strip() if value is not None else ""
            if text_value:
                is_blank = False
            normalized_row[normalized_key] = text_value

        if not is_blank:
            records.append(normalized_row)

    return CsvParseResult(
        delimiter=";",
        headers_raw=headers_raw,
        headers_normalized=headers_normalized,
        records=records,
        row_count=len(records),
    )


def sample_rows(records: list[dict[str, str]], *, limit: int = 10) -> list[dict[str, str]]:
    return records[: max(1, limit)]

