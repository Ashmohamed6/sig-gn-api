import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen


class KoboSyncError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class KoboFormTarget:
    dataset_code: str
    asset_uid: str
    label: str | None = None


def normalize_dataset_code(value: str | None) -> str:
    return str(value or "").strip().lower()


def parse_iso_datetime(value: Any) -> datetime | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def extract_submission_datetime(record: Mapping[str, Any]) -> datetime | None:
    candidates: list[Any] = [
        record.get("_submission_time"),
        record.get("_submitted_at"),
        record.get("submission_time"),
        record.get("submitted_at"),
        record.get("SubmissionDate"),
        record.get("start"),
        record.get("__system/submissionDate"),
        record.get("__system/submission_date"),
    ]

    system_data = record.get("__system")
    if isinstance(system_data, Mapping):
        candidates.extend(
            [
                system_data.get("submissionDate"),
                system_data.get("submission_date"),
                system_data.get("submitted_at"),
            ]
        )

    for value in candidates:
        parsed = parse_iso_datetime(value)
        if parsed is not None:
            return parsed
    return None


def filter_records_since(records: Iterable[Mapping[str, Any]], since: datetime) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    since_utc = since.astimezone(timezone.utc) if since.tzinfo else since.replace(tzinfo=timezone.utc)
    kept: list[Mapping[str, Any]] = []
    skipped_before = 0
    missing_date = 0

    for record in records:
        submitted_at = extract_submission_datetime(record)
        if submitted_at is None:
            # On conserve les enregistrements sans date explicite pour eviter une perte de donnees.
            kept.append(record)
            missing_date += 1
            continue

        if submitted_at >= since_utc:
            kept.append(record)
        else:
            skipped_before += 1

    return kept, {
        "skipped_before_since": skipped_before,
        "missing_submission_date": missing_date,
    }


def _extract_asset_uid(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        for key in ("asset_uid", "uid", "asset", "id_string"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                return raw.strip()
    return ""


def _extract_label(value: Any, fallback: str | None = None) -> str | None:
    if isinstance(value, Mapping):
        label = value.get("label")
        if isinstance(label, str) and label.strip():
            return label.strip()
    return fallback


def _parse_project_forms(bucket: Any) -> dict[str, KoboFormTarget]:
    out: dict[str, KoboFormTarget] = {}

    if isinstance(bucket, Mapping):
        for dataset_raw, cfg in bucket.items():
            dataset_code = normalize_dataset_code(str(dataset_raw))
            if not dataset_code:
                continue
            asset_uid = _extract_asset_uid(cfg)
            if not asset_uid:
                continue
            out[dataset_code] = KoboFormTarget(
                dataset_code=dataset_code,
                asset_uid=asset_uid,
                label=_extract_label(cfg, fallback=str(dataset_raw)),
            )
        return out

    if isinstance(bucket, list):
        for item in bucket:
            if not isinstance(item, Mapping):
                continue
            dataset_code = normalize_dataset_code(item.get("dataset_code"))
            asset_uid = _extract_asset_uid(item)
            if not dataset_code or not asset_uid:
                continue
            out[dataset_code] = KoboFormTarget(
                dataset_code=dataset_code,
                asset_uid=asset_uid,
                label=_extract_label(item, fallback=dataset_code),
            )
    return out


def list_project_forms(registry: Any, project_code: str) -> list[KoboFormTarget]:
    if not isinstance(registry, Mapping):
        return []

    project_key = str(project_code or "").strip().upper()
    if not project_key:
        return []

    scoped_forms: dict[str, KoboFormTarget] = {}
    for key in (project_key, project_key.lower()):
        scoped_forms.update(_parse_project_forms(registry.get(key)))
    if scoped_forms:
        return sorted(scoped_forms.values(), key=lambda f: f.dataset_code)

    # Fallback: mapping global dataset -> asset_uid.
    fallback_forms = _parse_project_forms(registry)
    return sorted(fallback_forms.values(), key=lambda f: f.dataset_code)


def resolve_project_form(registry: Any, project_code: str, dataset_code: str) -> KoboFormTarget | None:
    normalized = normalize_dataset_code(dataset_code)
    if not normalized:
        return None
    for form in list_project_forms(registry, project_code):
        if form.dataset_code == normalized:
            return form
    return None


class KoboClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: int = 20):
        self.base_url = str(base_url or "").rstrip("/")
        self.token = str(token or "").strip()
        self.timeout_seconds = max(3, int(timeout_seconds))

        if not self.base_url:
            raise KoboSyncError("KOBO_BASE_URL manquant.")
        if not self.token:
            raise KoboSyncError("KOBO_API_TOKEN manquant.")

    def _request_json(self, url: str) -> Mapping[str, Any]:
        full_url = url if url.startswith("http://") or url.startswith("https://") else urljoin(f"{self.base_url}/", url.lstrip("/"))
        req = Request(
            full_url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Token {self.token}",
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                payload = json.loads(raw or "{}")
        except HTTPError as exc:
            detail = exc.reason if getattr(exc, "reason", None) else "Erreur HTTP Kobo"
            try:
                body = exc.read().decode("utf-8", errors="replace")
                if body:
                    parsed = json.loads(body)
                    if isinstance(parsed, Mapping):
                        detail = str(parsed.get("detail") or parsed.get("message") or detail)
            except Exception:
                pass
            raise KoboSyncError(f"Kobo API HTTP {exc.code}: {detail}", status_code=exc.code) from exc
        except URLError as exc:
            raise KoboSyncError(f"Kobo API inaccessible: {exc.reason}") from exc
        except TimeoutError as exc:
            raise KoboSyncError("Kobo API timeout.") from exc
        except json.JSONDecodeError as exc:
            raise KoboSyncError("Reponse Kobo invalide (JSON attendu).") from exc

        if not isinstance(payload, Mapping):
            raise KoboSyncError("Reponse Kobo invalide (objet JSON attendu).")
        return payload

    def fetch_asset_submissions(self, asset_uid: str, *, limit: int = 500) -> list[Mapping[str, Any]]:
        uid = str(asset_uid or "").strip()
        if not uid:
            raise KoboSyncError("Asset Kobo manquant.")

        max_records = max(1, int(limit))
        page_size = min(max_records, 200)
        next_url: str | None = f"{self.base_url}/api/v2/assets/{quote(uid)}/data/?format=json&limit={page_size}"
        rows: list[Mapping[str, Any]] = []

        while next_url and len(rows) < max_records:
            payload = self._request_json(next_url)
            results = payload.get("results")
            if not isinstance(results, list):
                raise KoboSyncError("Reponse Kobo invalide (champ 'results' absent).")

            for item in results:
                if isinstance(item, Mapping):
                    rows.append(dict(item))
                    if len(rows) >= max_records:
                        break

            raw_next = payload.get("next")
            next_url = str(raw_next).strip() if isinstance(raw_next, str) and raw_next.strip() else None

        return rows
