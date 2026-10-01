import unittest
from datetime import date, timedelta

from card_logger import insight
from card_logger.db import Card, CardDatabase
from card_logger.meta import Deck, DeckCard, LEGEND, MAIN, MetaTracker

ANCHOR = date(2026, 9, 20)
WEEKS = 6


def day(n):
    return (ANCHOR - timedelta(days=n)).isoformat()


def build(meta):
    """42 days x 2 decks a day, deterministic patterns:

    - Winner Card: in most top-quarter decks, few others; those decks win more.
    - Climber: play rate rises steadily from ~0% to ~60% of decks.
    - Rising legend "Yi" grows from rare to common; "Yi Core" is in all Yi lists only.
    - Newbie: only appears in the last 10 days.
    - Staple: in every deck (already meta, never a candidate).
    - Filler: in every other deck throughout (no signal).
    """
    n = 0
    for d in range(41, -1, -1):
        t = (41 - d) / 41  # 0 oldest .. 1 newest
        for k in range(2):
            n += 1
            placement = (n * 7) % 32 + 1          # spread over 1..32
            top = placement <= 8
            yi = (n % 10) < round(1 + 5 * t)       # Yi share grows ~10% -> ~60%
            cards = ["Staple"]
            if n % 2:
                cards.append("Filler")
            if (top and n % 5 != 0) or (not top and n % 7 == 0):
                cards.append("Winner Card")
            if (n * 37) % 100 < 60 * t:
                cards.append("Climber")
            if yi:
                cards.append("Yi Core")
            if d <= 10 and n % 3 == 0:
                cards.append("Newbie")
            winner = "Winner Card" in cards
            wins, losses = (5, 1) if winner else (3, 3)
            meta.add_deck(Deck(
                legend="Master Yi, Wuju Bladesman" if yi else "Kennen, Heart of the Tempest",
                date=day(d), placement=placement, players=32, wins=wins, losses=losses, ties=0,
                cards=[DeckCard(LEGEND, "Master Yi, Wuju Bladesman" if yi else "Kennen, Heart of the Tempest", 1)]
                + [DeckCard(MAIN, c, 3) for c in cards],
            ))


class InsightTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(":memory:")
        cls.meta = MetaTracker(cls.db)
        build(cls.meta)
        cls.db.add(Card(name="Climber", game="Riftbound", quantity=2))
        for i, price in enumerate([1.0, 1.0, 1.1, 1.3, 1.6]):  # unplayed card, price climbing
            cls.db.record_price("Hidden Gem", price, day=(ANCHOR - timedelta(days=16 - i * 4)).isoformat())
        for i in range(5):  # unplayed card, flat price
            cls.db.record_price("Boring Card", 2.0, day=(ANCHOR - timedelta(days=16 - i * 4)).isoformat())
        cls.report = insight.analyse(cls.db, cls.meta, weeks=WEEKS, today=ANCHOR)
        cls.by_name = {c.name: c for c in cls.report.candidates}

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def kinds(self, name):
        return {s.kind for s in self.by_name[name].signs}

    def test_counts(self):
        self.assertEqual(self.report.decks, 84)
        self.assertEqual(self.report.with_placing, 84)
        self.assertEqual(self.report.with_record, 84)
        self.assertEqual(self.report.message, "")
        self.assertEqual(self.report.span_days, 42)

    def test_winner_card(self):
        self.assertIn(insight.TOPCUT, self.kinds("Winner Card"))
        self.assertIn(insight.WINRATE, self.kinds("Winner Card"))
        self.assertGreater(self.by_name["Winner Card"].win_rate, 0.7)
        self.assertNotIn(insight.CLIMB, self.kinds("Winner Card"))  # 29% -> 31% is noise, not a climb

    def test_climber(self):
        c = self.by_name["Climber"]
        self.assertIn(insight.CLIMB, self.kinds("Climber"))
        self.assertGreater(c.slope, 2)
        self.assertEqual(c.owned, 2)

    def test_rising_legend(self):
        yi = next(t for t in self.report.legends if t.legend.startswith("Master Yi"))
        self.assertTrue(yi.rising)
        self.assertEqual(self.report.legends[0].legend, yi.legend)  # biggest riser first
        self.assertIn(insight.LEGEND_PULL, self.kinds("Yi Core"))

    def test_new_arrival(self):
        self.assertIn(insight.NEW, self.kinds("Newbie"))

    def test_no_signal_cards(self):
        self.assertNotIn("Staple", self.by_name)  # already everywhere
        self.assertNotIn("Filler", self.by_name)  # steady
        self.assertNotIn("Boring Card", self.by_name)

    def test_speculation(self):
        gem = self.by_name["Hidden Gem"]
        self.assertEqual([s.kind for s in gem.signs], [insight.SPECULATION])
        self.assertAlmostEqual(gem.price_move, 0.6)

    def test_sorted_by_score(self):
        scores = [c.score for c in self.report.candidates]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 < s <= 100 for s in scores))

    def test_first_import_has_no_history_signs(self):
        # Like a first riftbound.gg import: many decks, all from the last 3 days.
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        for n in range(60):
            cards = ["Common"] + (["Rare Pick"] if n % 4 == 0 else [])
            meta.add_deck(Deck(legend=f"Legend {n % 5}", date=day(n % 3),
                               cards=[DeckCard(LEGEND, f"Legend {n % 5}", 1)] + [DeckCard(MAIN, c, 3) for c in cards]))
        report = insight.analyse(db, meta, weeks=6, today=ANCHOR)
        self.assertEqual(report.span_days, 3)
        self.assertIn("cover the last 3 days", report.message)
        self.assertEqual(report.legends, [])
        kinds = {s.kind for c in report.candidates for s in c.signs}
        self.assertFalse(kinds & {insight.NEW, insight.SPREAD, insight.CLIMB, insight.LEGEND_PULL}, kinds)
        db.close()

    def test_not_enough_decks(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        meta.add_deck(Deck(date=day(1), cards=[DeckCard(MAIN, "A", 1)]))
        report = insight.analyse(db, meta, today=ANCHOR)
        self.assertIn("at least 20", report.message)
        self.assertEqual(report.candidates, [])
        empty = insight.analyse(CardDatabase(":memory:"), MetaTracker(CardDatabase(":memory:")))
        self.assertIn("No decklists", empty.message)


class InvestmentsTest(unittest.TestCase):
    def test_good_investments(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        build(meta)
        inv = insight.investments(db, meta, {}, today=ANCHOR)
        good = {p.name: p for p in inv.good}
        self.assertEqual(inv.good[0].name, "Winner Card")
        self.assertEqual((good["Winner Card"].verdict, good["Winner Card"].stars), ("Strong buy", "★★★"))
        self.assertEqual(good["Yi Core"].verdict, "Buy")
        self.assertIn("Key card of a legend on the rise", [r.short for r in good["Yi Core"].reasons])
        self.assertNotIn("Staple", good)
        self.assertNotIn("Filler", good)

    def test_bad_investments(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        for n in range(60):
            recent = n % 2 == 0
            cards = ["Steady", "Pumped"] + ([] if recent else ["Fading"])
            meta.add_deck(Deck(legend="Kennen", date=day(n % 14 if recent else 14 + n % 14),
                               cards=[DeckCard(LEGEND, "Kennen", 1)] + [DeckCard(MAIN, c, 3) for c in cards]))
        for i, price in enumerate([4.0, 4.0, 4.1, 6.0]):  # spike with no change in play
            db.record_price("Pumped", price, day=day(12 - i * 4))
        db.add(Card(name="Pumped", game="Riftbound", quantity=4, value=6.0))
        inv = insight.investments(db, meta, {"fading": 5.0, "pumped": 6.0, "steady": 5.0}, today=ANCHOR)
        bad = {p.name: p for p in inv.bad}
        self.assertEqual(set(bad), {"Fading", "Pumped"})
        self.assertEqual((bad["Fading"].verdict, bad["Fading"].strength), ("Avoid", 3))  # 100% -> 0% of decks
        self.assertEqual(bad["Fading"].reasons[0].short, "Being played less")
        self.assertEqual(bad["Pumped"].verdict, "Sell")  # you own it
        self.assertEqual(bad["Pumped"].reasons[0].short, "Price jumped without more play")
        self.assertEqual(inv.good, [])

    def test_bulk_cards_left_out(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        for n in range(60):
            recent = n % 2 == 0
            meta.add_deck(Deck(legend="Kennen", date=day(n % 14 if recent else 14 + n % 14),
                               cards=[DeckCard(LEGEND, "Kennen", 1), DeckCard(MAIN, "Steady", 3)]
                               + ([] if recent else [DeckCard(MAIN, "Cheap Fading", 3)])))
        self.assertEqual(insight.investments(db, meta, {"cheapfading": 0.20}, today=ANCHOR).bad, [])


class PriceTrendTest(unittest.TestCase):
    def setUp(self):
        from card_logger import catalog
        self.catalog = catalog
        self.db = CardDatabase(":memory:")
        raw = [{"id": "OGN-001", "name": "Rising Card", "price": "3.00", "delta7dPrice": "1.00", "deltaPrice": "0.10"},
               {"id": "OGN-001a", "name": "Rising Card", "price": "9.00"},
               {"id": "OGN-002", "name": "Old Card", "price": "5.00"}]
        catalog.save(self.db, catalog.parse(raw), day="2026-09-27")

    def test_week_from_riftbound_changes(self):
        t = insight.price_trend(self.db, "Rising Card")  # the regular printing, not the $9 alt art
        self.assertEqual(t.points, [("2026-09-20", 2.0), ("2026-09-26", 2.9), ("2026-09-27", 3.0)])
        self.assertAlmostEqual(t.change, 0.5)
        self.assertEqual(t.days, 7)
        (start, now), (end, ahead) = t.projection
        self.assertEqual((start, now, end), ("2026-09-27", 3.0, "2026-10-04"))  # no further ahead than the history
        self.assertGreater(ahead, 3.0)
        self.assertLessEqual(ahead, 4.5)  # capped at +50%

    def test_no_trend_without_history(self):
        t = insight.price_trend(self.db, "Old Card")
        self.assertEqual(t.points, [("2026-09-27", 5.0)])
        self.assertIsNone(t.projection)
        self.assertEqual(insight.price_trend(self.db, "Unknown").points, [])

    def test_projection_follows_the_line(self):
        points = [(f"2026-09-{d:02d}", 10.0 - (d - 1) * 0.1) for d in range(1, 29)]  # -0.10 a day
        (_, now), (end, ahead) = insight._projection(points)
        self.assertEqual(end, "2026-10-12")
        self.assertAlmostEqual(ahead, now - 1.4, places=6)
        self.assertIsNone(insight._projection(points[:2]))


class HelpersTest(unittest.TestCase):
    def test_slope(self):
        self.assertAlmostEqual(insight.slope_per_week([(0, 0.1), (1, 0.2), (2, 0.3)]), 10)
        self.assertIsNone(insight.slope_per_week([(0, 0.1), (1, 0.2)]))

    def test_z(self):
        self.assertGreater(insight.two_prop_z(30, 40, 10, 40), 3)
        self.assertEqual(insight.two_prop_z(1, 0, 1, 1), 0.0)


if __name__ == "__main__":
    unittest.main()
