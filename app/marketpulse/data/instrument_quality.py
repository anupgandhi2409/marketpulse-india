"""Read-only data-quality summaries for parsed instrument-master records."""

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable

from marketpulse.data.instrument import Instrument


Distribution = tuple[tuple[str | None, int], ...]
FieldCounts = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class DuplicateSummary:
    """Counts for duplicate nonblank values of one identifier field."""

    distinct_duplicate_values: int
    affected_record_count: int
    sample_values: tuple[str, ...]


@dataclass(frozen=True)
class InstrumentQualityReport:
    """Deterministic, non-rejecting observations about instrument records."""

    total_records: int
    exchange_segment_distribution: Distribution
    market_segment_distribution: Distribution
    missing_required_fields: FieldCounts
    missing_optional_fields: FieldCounts
    duplicate_instrument_tokens: DuplicateSummary
    duplicate_symbols: DuplicateSummary
    duplicate_trading_symbols: DuplicateSummary
    duplicate_isins: DuplicateSummary
    lot_size_distribution: Distribution
    numeric_zero_lot_size_count: int
    numeric_negative_lot_size_count: int
    series_distribution: Distribution
    blank_instrument_type_count: int
    instrument_type_distribution: Distribution


class InstrumentMasterQualityReporter:
    """Summarize data-quality observations without changing or rejecting records."""

    _REQUIRED_FIELDS = (
        "instrument_token",
        "exchange_segment",
        "symbol",
        "trading_symbol",
    )
    _OPTIONAL_FIELDS = (
        "series",
        "isin",
        "description",
        "market_segment",
        "lot_size",
    )
    _DUPLICATE_FIELDS = (
        ("instrument_token", "duplicate_instrument_tokens"),
        ("symbol", "duplicate_symbols"),
        ("trading_symbol", "duplicate_trading_symbols"),
        ("isin", "duplicate_isins"),
    )
    _SAMPLE_SIZE = 5

    def analyze(
        self,
        instruments: Iterable[Instrument],
        p_inst_type_values: Iterable[str | None],
    ) -> InstrumentQualityReport:
        """Return quality observations for records and aligned source type values."""
        records = tuple(instruments)
        instrument_types = tuple(p_inst_type_values)
        if len(records) != len(instrument_types):
            raise ValueError(
                "p_inst_type_values must contain one value for each instrument record."
            )

        missing_required = self._missing_counts(records, self._REQUIRED_FIELDS)
        missing_optional = self._missing_counts(records, self._OPTIONAL_FIELDS)
        duplicates = {
            report_field: self._duplicate_summary(
                getattr(record, field) for record in records
            )
            for field, report_field in self._DUPLICATE_FIELDS
        }
        lot_sizes = [record.lot_size for record in records]
        zero_count, negative_count = self._lot_size_sign_counts(lot_sizes)

        return InstrumentQualityReport(
            total_records=len(records),
            exchange_segment_distribution=self._distribution(
                record.exchange_segment for record in records
            ),
            market_segment_distribution=self._distribution(
                record.market_segment for record in records
            ),
            missing_required_fields=missing_required,
            missing_optional_fields=missing_optional,
            duplicate_instrument_tokens=duplicates["duplicate_instrument_tokens"],
            duplicate_symbols=duplicates["duplicate_symbols"],
            duplicate_trading_symbols=duplicates["duplicate_trading_symbols"],
            duplicate_isins=duplicates["duplicate_isins"],
            lot_size_distribution=self._distribution(lot_sizes),
            numeric_zero_lot_size_count=zero_count,
            numeric_negative_lot_size_count=negative_count,
            series_distribution=self._distribution(record.series for record in records),
            blank_instrument_type_count=sum(
                1 for value in instrument_types if self._is_blank(value)
            ),
            instrument_type_distribution=self._distribution(instrument_types),
        )

    @classmethod
    def _missing_counts(
        cls,
        records: tuple[Instrument, ...],
        fields: tuple[str, ...],
    ) -> FieldCounts:
        return tuple(
            (
                field,
                sum(1 for record in records if cls._is_blank(getattr(record, field))),
            )
            for field in fields
        )

    @classmethod
    def _duplicate_summary(cls, values: Iterable[str | None]) -> DuplicateSummary:
        counts = Counter(value for value in values if not cls._is_blank(value))
        duplicate_counts = sorted(
            ((value, count) for value, count in counts.items() if count > 1),
            key=lambda item: item[0],
        )
        return DuplicateSummary(
            distinct_duplicate_values=len(duplicate_counts),
            affected_record_count=sum(count for _, count in duplicate_counts),
            sample_values=tuple(
                value for value, _ in duplicate_counts[: cls._SAMPLE_SIZE]
            ),
        )

    @staticmethod
    def _distribution(values: Iterable[str | None]) -> Distribution:
        counts = Counter(
            None if InstrumentMasterQualityReporter._is_blank(value) else value
            for value in values
        )
        return tuple(
            sorted(counts.items(), key=lambda item: (item[0] is not None, item[0] or ""))
        )

    @staticmethod
    def _is_blank(value: str | None) -> bool:
        return value is None or not value.strip()

    @staticmethod
    def _lot_size_sign_counts(values: Iterable[str | None]) -> tuple[int, int]:
        zero_count = 0
        negative_count = 0
        for value in values:
            if value is None:
                continue
            try:
                number = Decimal(value.strip())
            except (InvalidOperation, ValueError):
                continue
            if not number.is_finite():
                continue
            if number == 0:
                zero_count += 1
            elif number < 0:
                negative_count += 1
        return zero_count, negative_count
