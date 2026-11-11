import unittest

from core.price_utils import (
    currency_symbol,
    detect_currency,
    format_cents,
    parse_price_to_cents,
)


class PriceUtilsTests(unittest.TestCase):
    def test_parse_common_formats(self):
        self.assertEqual(parse_price_to_cents("$59.99"), 5999)
        self.assertEqual(parse_price_to_cents("59,99 €"), 5999)
        self.assertEqual(parse_price_to_cents("26.25 USD"), 2625)
        self.assertEqual(parse_price_to_cents("1.234,56 €"), 123456)
        self.assertEqual(parse_price_to_cents("1,234.56"), 123456)
        self.assertEqual(parse_price_to_cents("0"), 0)

    def test_parse_missing_or_garbage(self):
        self.assertIsNone(parse_price_to_cents(None))
        self.assertIsNone(parse_price_to_cents(""))
        self.assertIsNone(parse_price_to_cents("free"))

    def test_format_usd_and_eur(self):
        self.assertEqual(format_cents(5999, "USD"), "$59.99")
        self.assertEqual(format_cents(5999, "EUR"), "59.99 €")
        self.assertEqual(format_cents(5999, "EUR", locale="it_IT"), "59,99 €")
        self.assertEqual(format_cents(1000, "JPY"), "¥10")

    def test_symbol_and_detect_currency(self):
        self.assertEqual(currency_symbol("EUR"), "€")
        self.assertEqual(currency_symbol("XYZ"), "XYZ")
        self.assertEqual(detect_currency("$59.99"), "USD")
        self.assertEqual(detect_currency("59,99 €"), "EUR")
        self.assertEqual(detect_currency("26.25 USD"), "USD")


if __name__ == "__main__":
    unittest.main()
