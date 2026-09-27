import unittest
from datetime import date, timedelta

from card_logger import currency
from card_logger.db import Card, CardDatabase
from card_logger.market import (
    BUY_EARLY, HOLD, HOLD_RISING, NO_SIGNAL, SELL_FALLING, SELL_HYPE, SELL_SPIKE, WATCH,
    MetaTrend, PriceChange, analyse, decide, meta_trends, play_rate_series, price_change,
)
from card_logger.meta import MetaTracker, parse_decklist
from card_logger.pricing import parse_riftbound_market

ANCHOR = date(2026, 9, 20)


def day(n_days_ago):
    return (ANCHOR - timedelta(days=n_days_ago)).isoformat()


class DecideTest(unittest.TestCase):
    rising = MetaTrend("X", 0.40, 0.10, 20, 20)    # +30 pts
    falling = MetaTrend("X", 0.10, 0.40, 20, 20)   # -30 pts
    steady = MetaTrend("X", 0.30, 0.28, 20, 20)
    thin = MetaTrend("X", 0.50, 0.00, 2, 1)        # too few decklists

    def test_owned(self):
        spike, flat = PriceChange(10, 15, 14), PriceChange(10, 10.5, 14)
        self.assertEqual(decide(2, self.rising, spike)[0], SELL_HYPE)
        self.assertEqual(decide(2, self.rising, flat)[0], HOLD_RISING)
        self.assertEqual(decide(2, self.rising, None)[0], HOLD_RISING)
        self.assertEqual(decide(2, self.falling, flat)[0], SELL_FALLING)
        self.assertEqual(decide(2, self.steady, spike)[0], SELL_SPIKE)
        self.assertEqual(decide(2, None, spike)[0], SELL_SPIKE)
        self.assertEqual(decide(2, self.steady, flat)[0], HOLD)
        self.assertEqual(decide(2, self.thin, flat)[0], HOLD)  # not enough data to call it rising
        self.assertEqual(decide(2, None, None)[0], NO_SIGNAL)

    def test_not_owned(self):
        self.assertEqual(decide(0, self.rising, PriceChange(10, 10.2, 14))[0], BUY_EARLY)
        self.assertEqual(decide(0, self.rising, None)[0], BUY_EARLY)
        self.assertEqual(decide(0, self.rising, PriceChange(10, 13, 14))[0], WATCH)
        self.assertEqual(decide(0, self.steady, None)[0], NO_SIGNAL)
        self.assertEqual(decide(0, self.falling, None)[0], NO_SIGNAL)

    def test_small_samples_are_not_trends(self):
        noisy = MetaTrend("X", 0.95, 0.82, 28, 28)  # +13 pts, but z is about 1.5
        self.assertFalse(noisy.significant)
        self.assertEqual(decide(2, noisy, PriceChange(10, 10, 14))[0], HOLD)
        self.assertIn("normal variation", decide(2, noisy, None)[1])
        self.assertEqual(decide(0, noisy, None)[0], NO_SIGNAL)
        self.assertTrue(self.rising.significant)
        self.assertGreater(MetaTrend("X", 0.95, 0.82, 200, 200).z_score, 2)  # same change, more data

    def test_reason_mentions_numbers(self):
        _, reason = decide(2, self.rising, PriceChange(10, 15, 14))
        self.assertIn("+30 pts", reason)
        self.assertIn("+50%", reason)


class PriceChangeTest(unittest.TestCase):
    def test_uses_price_from_period_start(self):
        history = [(day(40), 5.0), (day(20), 8.0), (day(10), 9.0), (day(0), 12.0)]
        c = price_change(history, 14)
        self.assertEqual((c.start, c.end, c.days), (8.0, 12.0, 20))
        self.assertAlmostEqual(c.fraction, 0.5)

    def test_short_history_uses_oldest(self):
        c = price_change([(day(3), 4.0), (day(0), 5.0)], 30)
        self.assertEqual((c.start, c.days), (4.0, 3))

    def test_not_enough(self):
        self.assertIsNone(price_change([(day(0), 5.0)], 14))
        self.assertIsNone(price_change([], 14))


class TrendTest(unittest.TestCase):
    def setUp(self):
        self.db = CardDatabase(":memory:")
        self.meta = MetaTracker(self.db)
        # Previous window (15-28 days before the newest deck): 10 decks, 1 plays Rising Star.
        # Recent window (0-14 days): 10 decks, 6 play Rising Star; Old Staple drops from 8 to 2.
        for i in range(10):
            self.add(15 + i, ["Filler"] + (["Rising Star"] if i < 1 else []) + (["Old Staple"] if i < 8 else []))
            self.add(i, ["Filler"] + (["Rising Star"] if i < 6 else []) + (["Old Staple"] if i < 2 else []))

    def tearDown(self):
        self.db.close()

    def add(self, days_ago, cards):
        deck = parse_decklist("Legend: Kennen, Heart of the Tempest\nRunes: 12 Chaos Rune\nMain Deck:\n"
                              + "\n".join(f"3 {c}" for c in cards))
        deck.date = day(days_ago)
        self.meta.add_deck(deck)

    def test_trends(self):
        t = meta_trends(self.meta, days=14)
        self.assertNotIn("kennenheartofthetempest", t)  # legends and runes aren't cards played
        self.assertNotIn("chaosrune", t)
        rs = t["risingstar"]
        self.assertEqual((rs.recent_decks, rs.previous_decks), (10, 10))
        self.assertAlmostEqual(rs.change_points, 50)
        self.assertAlmostEqual(t["oldstaple"].change_points, -60)
        self.assertAlmostEqual(t["filler"].change_points, 0)

    def test_series(self):
        series = play_rate_series(self.meta, "Rising Star", buckets=4)
        self.assertEqual(len(series), 4)
        self.assertEqual(series[-1][0], ANCHOR.isoformat())
        self.assertGreater(series[-1][1], series[0][1])

    def test_analyse_combines_everything(self):
        self.db.add(Card(name="Old Staple", game="Riftbound", quantity=4, value=6.0, purchase_price=2.0))
        self.db.add(Card(name="Rising Star", game="Riftbound", quantity=1, value=3.0))
        self.db.record_price("Rising Star", 3.0, day=day(20))
        self.db.record_price("Rising Star", 3.1, day=day(0))
        self.db.record_price("Filler", 1.0, day=day(20))
        self.db.record_price("Filler", 1.0, day=day(0))
        self.db.set_setting("currency", "USD")
        currency.configure(self.db)
        rows = {r.name: r for r in analyse(self.db, self.meta, days=14)}
        self.assertEqual(rows["Old Staple"].signal, SELL_FALLING)
        self.assertEqual(rows["Old Staple"].profit, 16.0)
        self.assertEqual(rows["Rising Star"].signal, HOLD_RISING)
        self.assertIsNone(rows["Rising Star"].profit)  # purchase price unknown
        self.assertEqual(rows["Filler"].owned, 0)
        self.assertEqual(rows["Filler"].value, 1.0)
        self.assertEqual(list(rows)[0], "Old Staple")  # sell signals come first

    def test_no_decks(self):
        empty = MetaTracker(CardDatabase(":memory:"))
        self.assertEqual(meta_trends(empty), {})
        self.assertEqual(play_rate_series(empty, "X"), [])


class PriceHistoryTest(unittest.TestCase):
    def setUp(self):
        self.db = CardDatabase(":memory:")

    def tearDown(self):
        self.db.close()

    def test_card_value_changes_are_recorded_once_per_day(self):
        card = Card(name="Gust Monk", value=1.0)
        self.db.add(card)
        card.value = 1.5
        self.db.update(card)
        self.assertEqual(self.db.price_history(card_id=card.id), [(date.today().isoformat(), 1.5)])
        self.db.record_price("Gust Monk", 2.0, card_id=card.id, day="2026-01-01")
        self.assertEqual([p for _, p in self.db.price_history(card_id=card.id)], [2.0, 1.5])
        self.db.delete(card.id)
        self.assertEqual(self.db.price_history(card_id=card.id), [])

    def test_name_history_is_separate_and_spelling_insensitive(self):
        self.db.record_price("Kennen, Storm of Shuriken", 4.0, day="2026-09-01")
        self.db.record_price("Kennen - Storm of Shuriken", 5.0, day="2026-09-02")
        self.assertEqual(self.db.price_history(name="kennen storm of shuriken"),
                         [("2026-09-01", 4.0), ("2026-09-02", 5.0)])
        self.assertEqual(self.db.last_price_update(), "2026-09-02")

    def test_zero_value_not_recorded(self):
        card = Card(name="Unpriced")
        self.db.add(card)
        self.assertEqual(self.db.price_history(card_id=card.id), [])

    def test_settings(self):
        self.assertEqual(self.db.get_setting("x", "d"), "d")
        self.db.set_setting("x", "1")
        self.assertEqual(self.db.get_setting("x"), "1")


class SoldPricesTest(unittest.TestCase):
    def test_log_and_average(self):
        from card_logger.market import recent_sold
        db = CardDatabase(":memory:")
        db.add_sold_price("Kennen, Storm of Shuriken", 5.0, day=day(40), note="old")
        db.add_sold_price("Kennen - Storm of Shuriken", 4.0, day=day(5))
        sid = db.add_sold_price("kennen storm of shuriken", 6.0, day=day(1), note="NM")
        sold = db.sold_prices("Kennen, Storm of Shuriken")
        self.assertEqual([s["price"] for s in sold], [6.0, 4.0, 5.0])  # newest first, any spelling
        self.assertEqual(recent_sold(sold, 30, today=ANCHOR), (5.0, 2, day(1)))
        self.assertEqual(recent_sold(sold[2:], 30, today=ANCHOR), (5.0, 1, day(40)))  # only old: newest alone
        self.assertIsNone(recent_sold([]))
        db.delete_sold_price(sid)
        self.assertEqual(len(db.sold_prices("Kennen, Storm of Shuriken")), 2)
        with self.assertRaises(ValueError):
            db.add_sold_price("X", 0)
        db.close()


class EbayUrlTest(unittest.TestCase):
    def test_sold_listings_url(self):
        from card_logger.market import ebay_sold_url
        url = ebay_sold_url("Kennen, Storm of Shuriken", "Riftbound", "ebay.co.uk")
        self.assertTrue(url.startswith("https://www.ebay.co.uk/sch/i.html?_nkw=Kennen%2C+Storm+of+Shuriken+riftbound"))
        self.assertIn("LH_Sold=1", url)
        self.assertIn("LH_Complete=1", url)
        self.assertIn("_nkw=Charizard&", ebay_sold_url("Charizard", "Pokémon", "ebay.com"))


class MarketSnapshotTest(unittest.TestCase):
    def test_cheapest_regular_printing(self):
        def product(pid, name, number="1"):
            return {"productId": pid, "name": name, "extendedData": [{"name": "Number", "value": number}]}
        data = [
            ([product(1, "Gust Monk"), product(2, "Kennen, Storm of Shuriken"),
              product(3, "Kennen, Storm of Shuriken (Alternate Art)"),
              {"productId": 4, "name": "Booster Box", "extendedData": []}],
             [{"productId": 1, "subTypeName": "Normal", "marketPrice": 0.20},
              {"productId": 1, "subTypeName": "Foil", "marketPrice": 1.0},
              {"productId": 2, "subTypeName": "Normal", "marketPrice": 4.0},
              {"productId": 3, "subTypeName": "Foil", "marketPrice": 60.0},
              {"productId": 4, "subTypeName": "Normal", "marketPrice": 100.0}]),
            ([product(10, "Gust Monk")], [{"productId": 10, "subTypeName": "Normal", "marketPrice": 0.15}]),
        ]
        prices = parse_riftbound_market(data)
        self.assertEqual(prices, {"gustmonk": ("Gust Monk", 0.15),
                                  "kennenstormofshuriken": ("Kennen, Storm of Shuriken", 4.0)})


if __name__ == "__main__":
    unittest.main()


class PoundsTest(unittest.TestCase):
    def test_profit_uses_paid_in_pounds(self):
        db = CardDatabase(":memory:")
        db.set_setting("currency", "GBP")
        db.set_setting("fx_rates", '{"rates": {"USD": 1, "GBP": 0.8, "EUR": 0.9}, "day": "2026-09-25"}')
        currency.configure(db)
        # Worth $10 = £8 each; paid £5 each for 2 copies: £6 profit.
        db.add(Card(name="Pricey", game="Riftbound", quantity=2, value=10.0, purchase_price=5.0))
        row = analyse(db, MetaTracker(db), days=14)[0]
        self.assertAlmostEqual(currency.from_usd(row.profit), 6.0)
        self.assertEqual(currency.fmt(row.paid), "£5.00")
