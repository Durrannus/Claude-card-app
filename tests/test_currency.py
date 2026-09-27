import json
import unittest

from card_logger import currency
from card_logger.db import Card, CardDatabase

FRANKFURTER = {"amount": 1.0, "base": "USD", "date": "2026-09-25", "rates": {"EUR": 0.87696, "GBP": 0.75458}}
ER_API = {"result": "success", "rates": {"USD": 1, "GBP": 0.755093, "EUR": 0.877506}}


class CurrencyTest(unittest.TestCase):
    def setUp(self):
        self.db = CardDatabase(":memory:")
        currency.configure(self.db)

    def tearDown(self):
        self.db.close()

    def test_defaults_to_pounds_with_rough_rates(self):
        self.assertEqual(currency.code(), "GBP")
        self.assertEqual(currency.fmt(100), "£75.00")
        self.assertIn("approximate", currency.describe())

    def test_download_and_convert(self):
        text = currency.update_rates(self.db, fetch=lambda url: FRANKFURTER)
        self.assertIn("2026-09-25", text)
        self.assertEqual(currency.fmt(1266.68), "£955.81")
        self.assertEqual(currency.fmt_eur(10), f"£{10 / 0.87696 * 0.75458:,.2f}")
        self.assertAlmostEqual(currency.from_usd(currency.to_usd(40.0)), 40.0)
        self.assertFalse(currency.rates_due(self.db))
        currency.configure(self.db)  # reloads the saved rates
        self.assertEqual(currency.fmt(1266.68), "£955.81")

    def test_falls_back_to_second_source(self):
        def fetch(url):
            if "frankfurter" in url:
                raise OSError("down")
            return ER_API
        rates, _day, source = currency.fetch_rates(fetch)
        self.assertEqual(rates["GBP"], 0.755093)
        self.assertIn("ExchangeRate-API", source)

    def test_all_sources_down(self):
        def fetch(url):
            raise OSError("offline")
        with self.assertRaises(currency.RateError):
            currency.fetch_rates(fetch)
        self.assertTrue(currency.rates_due(self.db))

    def test_parse_and_negative(self):
        self.assertEqual(currency.parse("£1,234.50"), 1234.5)
        self.assertEqual(currency.parse(" $4 "), 4.0)
        self.assertEqual(currency.parse(""), 0.0)
        self.assertEqual(currency.fmt_local(-3), "−£3.00")

    def test_switching_converts_amounts_you_typed(self):
        self.db.set_setting("fx_rates", json.dumps({"rates": {"USD": 1, "GBP": 0.5, "EUR": 0.8}, "day": "x"}))
        currency.configure(self.db)
        card_id = self.db.add(Card(name="A", value=10.0, purchase_price=4.0))
        self.db.add_sold_price("A", 6.0)
        currency.set_currency(self.db, "USD")
        self.assertEqual(currency.code(), "USD")
        self.assertEqual(self.db.get(card_id).purchase_price, 8.0)
        self.assertEqual(self.db.sold_prices("A")[0]["price"], 12.0)
        self.assertEqual(self.db.get(card_id).value, 10.0)  # market values are stored in dollars already
        currency.set_currency(self.db, "GBP")
        self.assertEqual(self.db.get(card_id).purchase_price, 4.0)
        currency.configure(self.db)
        self.assertEqual(currency.code(), "GBP")


if __name__ == "__main__":
    unittest.main()
