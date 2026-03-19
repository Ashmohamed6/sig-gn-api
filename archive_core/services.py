from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .models import TrendPolarity, ValueBehavior, PeriodGranularity


@dataclass(frozen=True)
class PeriodToken:
    granularity: str
    year: int
    month: int = 0

    def as_key(self) -> tuple[str, int, int]:
        return (self.granularity, self.year, self.month)


def parse_period_token(raw: str) -> PeriodToken:
    token = str(raw or "").strip()
    if not token:
        raise ValueError("Le format de periode doit etre YYYY ou YYYY-MM.")

    if len(token) == 4 and token.isdigit():
        year = int(token)
        if year < 2000 or year > 2100:
            raise ValueError("L'annee doit etre comprise entre 2000 et 2100.")
        return PeriodToken(granularity=PeriodGranularity.YEAR, year=year, month=0)

    if len(token) == 7 and token[4] == "-":
        year_part, month_part = token.split("-", 1)
        if not (year_part.isdigit() and month_part.isdigit() and len(month_part) == 2):
            raise ValueError("Le format mensuel attendu est YYYY-MM.")

        year = int(year_part)
        month = int(month_part)
        if year < 2000 or year > 2100:
            raise ValueError("L'annee doit etre comprise entre 2000 et 2100.")
        if month < 1 or month > 12:
            raise ValueError("Le mois doit etre compris entre 1 et 12.")

        return PeriodToken(granularity=PeriodGranularity.MONTH, year=year, month=month)

    raise ValueError("Le format de periode doit etre YYYY ou YYYY-MM.")


def previous_period(period: PeriodToken, behavior: str) -> Optional[PeriodToken]:
    if period.granularity != PeriodGranularity.MONTH:
        return None

    if behavior == ValueBehavior.CUMULATIVE_YEARLY:
        if period.month <= 1:
            return None
        return PeriodToken(granularity=PeriodGranularity.MONTH, year=period.year, month=period.month - 1)

    if behavior == ValueBehavior.CUMULATIVE_CONTINUOUS:
        if period.month > 1:
            return PeriodToken(granularity=PeriodGranularity.MONTH, year=period.year, month=period.month - 1)
        return PeriodToken(granularity=PeriodGranularity.MONTH, year=period.year - 1, month=12)

    return None


def to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def to_float_or_none(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def compute_delta(value_a: Optional[Decimal], value_b: Optional[Decimal]) -> Optional[Decimal]:
    if value_a is None or value_b is None:
        return None
    return value_b - value_a


def compute_delta_pct(value_a: Optional[Decimal], delta: Optional[Decimal]) -> Optional[Decimal]:
    if value_a is None or delta is None:
        return None
    if value_a == Decimal("0"):
        return None
    return (delta / value_a) * Decimal("100")


def classify_trend(delta: Optional[Decimal], polarity: str) -> str:
    if delta is None:
        return "insufficient_data"

    epsilon = Decimal("0.000001")
    if abs(delta) <= epsilon:
        return "stable"

    if polarity == TrendPolarity.HIGHER_IS_BETTER:
        return "progression" if delta > 0 else "regression"

    if polarity == TrendPolarity.LOWER_IS_BETTER:
        return "progression" if delta < 0 else "regression"

    return "hausse" if delta > 0 else "baisse"
