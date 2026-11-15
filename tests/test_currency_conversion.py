import unittest
from decimal import Decimal

from marketpulse.analytics.currency_conversion import (
    CurrencyConversionError,
    convert_crudeoil_inr_to_indicative_usd,
)


class CurrencyConversionTests(unittest.TestCase):
    """Verify Decimal-only conversion and explicit USD/INR rate provenance."""

    def _convert(self, price=Decimal("8369"), rate=Decimal("95.005")):
        """Convert a fixture price with a labeled USDINR currency-futures rate."""
        return convert_crudeoil_inr_to_indicative_usd(
            price,
            rate,
            rate_source="Kotak Neo",
            rate_instrument_type="USD/INR currency futures",
        )

    def test_converts_using_decimal_and_identifies_rate_source(self):
        """Return the rounded indicative value with its supplied rate metadata."""
        result = self._convert()
        self.assertEqual(result.usd_per_barrel, Decimal("88.09"))
        self.assertEqual(result.inr_per_barrel, Decimal("8369"))
        self.assertEqual(result.inr_per_usd, Decimal("95.005"))
        self.assertEqual(result.rate_source, "Kotak Neo")
        self.assertEqual(result.rate_instrument_type, "USD/INR currency futures")
        self.assertIn("not a WTI or Brent quote", result.description)

    def test_rounding_uses_half_up_to_two_decimal_places(self):
        """Apply financial half-up rounding at the cent boundary."""
        self.assertEqual(self._convert(Decimal("1.005"), Decimal("1")).usd_per_barrel, Decimal("1.01"))
        self.assertEqual(self._convert(Decimal("1.004"), Decimal("1")).usd_per_barrel, Decimal("1.00"))

    def test_missing_or_invalid_rate_is_rejected(self):
        """Reject absent, nonpositive, and nonfinite rates without a fallback value."""
        for rate in (None, Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(rate=rate):
                with self.assertRaises(CurrencyConversionError):
                    self._convert(rate=rate)

    def test_unverified_freshness_does_not_block_indicative_conversion(self):
        """Convert a valid supplied rate without requiring a freshness assertion."""
        self.assertEqual(self._convert().usd_per_barrel, Decimal("88.09"))

    def test_invalid_crude_price_and_missing_provenance_are_rejected(self):
        """Require positive finite Decimal input and identified rate provenance."""
        for price in (Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity"), 8369):
            with self.subTest(price=price):
                with self.assertRaises(CurrencyConversionError):
                    self._convert(price=price)
        with self.assertRaises(CurrencyConversionError):
            convert_crudeoil_inr_to_indicative_usd(
                Decimal("8369"), Decimal("95"), rate_source=" ",
                rate_instrument_type="currency futures",
            )


if __name__ == "__main__":
    unittest.main()
