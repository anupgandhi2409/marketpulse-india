import csv
import io

from marketpulse.data.instrument import Instrument
from marketpulse.data.instrument_parser import InstrumentMasterParser
from marketpulse.data.instrument_quality import (
    InstrumentMasterQualityReporter,
    InstrumentQualityReport,
)
from marketpulse.data.instrument_store import InstrumentMasterStore


class InstrumentMasterService:
    """Load parsed instruments from storage and provide exact symbol lookups."""

    def __init__(
        self,
        store: InstrumentMasterStore,
        parser: InstrumentMasterParser,
        quality_reporter: InstrumentMasterQualityReporter | None = None,
    ) -> None:
        self._store = store
        self._parser = parser
        self._quality_reporter = (
            quality_reporter
            if quality_reporter is not None
            else InstrumentMasterQualityReporter()
        )

    def load_instruments(self, exchange_segment: str) -> list[Instrument]:
        csv_text = self._store.load_csv(exchange_segment)
        return self._parser.parse(csv_text)

    def get_quality_report(self, exchange_segment: str) -> InstrumentQualityReport:
        """Build a quality report from one stored CSV snapshot."""
        csv_text = self._store.load_csv(exchange_segment)
        instruments = self._parser.parse(csv_text)
        p_inst_type_values = self._extract_p_inst_type_values(csv_text)
        return self._quality_reporter.analyze(instruments, p_inst_type_values)

    @staticmethod
    def _extract_p_inst_type_values(csv_text: str) -> list[str]:
        csv_text = csv_text.removeprefix("\ufeff")
        reader = csv.reader(io.StringIO(csv_text, newline=""), strict=True)
        headers = next(reader, None)
        p_inst_type_index = (
            next(
                (index for index, header in enumerate(headers) if header.strip() == "pInstType"),
                None,
            )
            if headers is not None
            else None
        )
        if p_inst_type_index is None:
            raise ValueError(
                "Instrument master CSV is missing required quality-report column 'pInstType'."
            )
        return [row[p_inst_type_index] for row in reader]

    def find_by_symbol(self, exchange_segment: str, symbol: str) -> Instrument | None:
        return next(
            (
                instrument
                for instrument in self.load_instruments(exchange_segment)
                if instrument.symbol == symbol
            ),
            None,
        )

    def find_by_trading_symbol(
        self,
        exchange_segment: str,
        trading_symbol: str,
    ) -> Instrument | None:
        return next(
            (
                instrument
                for instrument in self.load_instruments(exchange_segment)
                if instrument.trading_symbol == trading_symbol
            ),
            None,
        )
