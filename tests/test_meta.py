import unittest

from card_logger.db import Card, CardDatabase
from card_logger.meta import (
    BATTLEFIELDS, CHAMPION, LEGEND, MAIN, RUNES, SIDEBOARD, MetaTracker, parse_decklist, typical_copies,
)

KENNEN = """
Legend: Kennen - Heart of the Tempest (x1)
Champion: 1 Kennen, Storm of Shuriken
## Main Deck (40)
3 Traveling Merchant
2x Tideturner
Gust Monk x3
Runes: 9 Chaos Rune, 3 Order Rune
Battlefields:
Forbidding Waste (210)
Minefield (OGN-212)
"""


def deck_text(legend, cards):
    return f"Legend:\n1 {legend}\nMain Deck:\n" + "\n".join(f"{q} {n}" for n, q in cards)


class ParseDecklistTest(unittest.TestCase):
    def test_sections_and_quantity_styles(self):
        deck = parse_decklist(KENNEN)
        self.assertEqual(deck.legend, "Kennen - Heart of the Tempest")
        got = [(c.section, c.name, c.quantity) for c in deck.cards]
        self.assertEqual(got, [
            (LEGEND, "Kennen - Heart of the Tempest", 1),
            (CHAMPION, "Kennen, Storm of Shuriken", 1),
            (MAIN, "Traveling Merchant", 3),
            (MAIN, "Tideturner", 2),
            (MAIN, "Gust Monk", 3),
            (RUNES, "Chaos Rune", 9),
            (RUNES, "Order Rune", 3),
            (BATTLEFIELDS, "Forbidding Waste", 1),
            (BATTLEFIELDS, "Minefield", 1),
        ])
        self.assertEqual(deck.main_count, 9)

    def test_no_headings_is_main_deck_and_duplicates_merge(self):
        deck = parse_decklist("2 Gust Monk\n1 gust monk\n\n// comment\nSideboard\n1 Tideturner")
        self.assertEqual([(c.section, c.name, c.quantity) for c in deck.cards],
                         [(MAIN, "Gust Monk", 3), (SIDEBOARD, "Tideturner", 1)])
        self.assertEqual(deck.legend, "")

    def test_card_named_like_heading_word_is_not_a_heading(self):
        deck = parse_decklist("Main Deck:\n1 Rune Prison\n2 Legendary Blade")
        self.assertEqual([c.name for c in deck.cards], ["Rune Prison", "Legendary Blade"])

    def test_cards_after_legend_and_champion_are_main_deck(self):
        deck = parse_decklist("Legend: Kennen - Heart of the Tempest\nChampion: 1 Kennen, Storm of Shuriken\n"
                              "3x Gust Monk\nLegend:\n1 Ignored Second Legend Line\n2 Tideturner")
        self.assertEqual([(c.section, c.name) for c in deck.cards], [
            (LEGEND, "Kennen - Heart of the Tempest"), (CHAMPION, "Kennen, Storm of Shuriken"),
            (MAIN, "Gust Monk"), (LEGEND, "Ignored Second Legend Line"), (MAIN, "Tideturner"),
        ])

    def test_typical_copies(self):
        self.assertEqual([typical_copies(x) for x in (0.4, 1.0, 2.5, 2.4, 3.7)], [1, 1, 3, 2, 4])

    def test_empty(self):
        self.assertEqual(parse_decklist("   \n").cards, [])


class MetaTrackerTest(unittest.TestCase):
    def setUp(self):
        self.db = CardDatabase(":memory:")
        self.meta = MetaTracker(self.db)

    def tearDown(self):
        self.db.close()

    def add(self, legend, cards, placement=None, day="2026-09-01", **kw):
        deck = parse_decklist(deck_text(legend, cards))
        deck.placement, deck.date = placement, day
        for k, v in kw.items():
            setattr(deck, k, v)
        return self.meta.add_deck(deck)

    def test_card_usage(self):
        self.add("Kennen, Heart of the Tempest", [("Gust Monk", 3), ("Tideturner", 2)], 1)
        self.add("Kennen - Heart of the Tempest", [("Gust Monk", 1)], 5)
        self.add("Master Yi, Wuju Bladesman", [("Gust Monk", 2), ("Zephyr Sage", 3)], 2)
        self.add("Irelia, Blade Dancer", [("Zephyr Sage", 3)], 9)
        self.db.add(Card(name="Gust Monk", game="Riftbound", quantity=2))
        self.db.add(Card(name="Gust Monk", game="Magic", quantity=5))  # other game: ignored
        self.db.add(Card(name="Tideturner", quantity=1))  # no game: counted
        self.db.add(Card(name="Zephyr Sage", game="Riftbound", quantity=4, wishlist=True))  # not owned

        usage = {u.name: u for u in self.meta.card_usage()}
        self.assertEqual(list(usage), ["Gust Monk", "Zephyr Sage", "Tideturner"])
        gm = usage["Gust Monk"]
        self.assertEqual((gm.decks, gm.share, gm.avg_copies, gm.total_copies, gm.owned), (3, 0.75, 2.0, 6, 2))
        self.assertEqual(usage["Tideturner"].owned, 1)
        self.assertEqual(usage["Zephyr Sage"].owned, 0)

        # Legend filter ignores punctuation differences.
        kennen = self.meta.card_usage(legend="Kennen, Heart of the Tempest")
        self.assertEqual([(u.name, u.decks) for u in kennen], [("Gust Monk", 2), ("Tideturner", 1)])
        # Placement filter.
        self.assertEqual({u.name for u in self.meta.card_usage(top=2)}, {"Gust Monk", "Tideturner", "Zephyr Sage"})
        self.assertEqual({u.name for u in self.meta.card_usage(top=1)}, {"Gust Monk", "Tideturner"})

    def test_runes_excluded_by_default(self):
        self.meta.add_deck(parse_decklist(KENNEN))
        names = {u.name for u in self.meta.card_usage()}
        self.assertNotIn("Chaos Rune", names)
        self.assertNotIn("Kennen - Heart of the Tempest", names)  # legend isn't a "card played"
        self.assertIn("Minefield", names)
        self.assertIn("Chaos Rune", {u.name for u in self.meta.card_usage(include_runes=True)})

    def test_legend_shares_and_since(self):
        self.add("Kennen, Heart of the Tempest", [("A", 1)], 3, day="2026-08-01")
        self.add("Kennen - Heart of the Tempest", [("A", 1)], 1, day="2026-09-10")
        self.add("Master Yi, Wuju Bladesman", [("A", 1)], None, day="2026-09-11")
        shares = self.meta.legend_shares()
        self.assertEqual([(s.decks, round(s.share, 2), s.best_placement) for s in shares], [(2, 0.67, 1), (1, 0.33, None)])
        self.assertEqual(len(self.meta.legends()), 2)
        self.assertEqual(len(self.meta.decks(since="2026-09-01")), 2)

    def test_store_get_delete(self):
        deck_id = self.add("Kennen, Heart of the Tempest", [("Gust Monk", 3)], 4, event="RQ", player="Sam")
        deck = self.meta.get_deck(deck_id)
        self.assertEqual((deck.event, deck.player, deck.placement, len(deck.cards)), ("RQ", "Sam", 4, 2))
        self.meta.delete_deck(deck_id)
        self.assertIsNone(self.meta.get_deck(deck_id))
        self.assertEqual(self.meta.card_usage(), [])

    def test_empty_deck_rejected(self):
        with self.assertRaises(ValueError):
            self.meta.add_deck(parse_decklist(""))


if __name__ == "__main__":
    unittest.main()
