import unittest

from card_logger import catalog
from card_logger.db import CardDatabase

# Trimmed real rows from riftbound.gg's card list.
RAW = [
    {"id": "UNL-131", "name": "Abandon", "set_name": "Unleashed", "rarity": "Uncommon", "type": ["Spell"],
     "price": "0.460000", "foilPrice": "3.830000", "deltaPrice": "-0.030000", "delta7dPrice": "-0.010000",
     "delta7dPriceFoil": "-0.080000", "cmPrice": "0.46", "image": "https://static.dotgg.gg/x.webp"},
    {"id": "VEN-168", "name": "Jinx - Demolitionist", "set_name": "Vendetta", "rarity": "Showcase", "type": ["Unit"],
     "price": "0.000000", "foilPrice": "111.220000", "deltaPrice": "0", "delta7dPrice": "0",
     "deltaFoilPrice": "1.000000", "delta7dPriceFoil": "-1.790002", "cmPrice": "148.99"},
    {"id": "SFD-001", "name": "Corina Veraza", "set_name": "Spiritforged", "rarity": "Epic", "type": ["Unit"],
     "price": "4.59", "foilPrice": "", "deltaPrice": "0.5", "delta7dPrice": "2.79"},
    {"id": "X-1", "name": "Unpriced", "price": None, "foilPrice": None},
    {"name": "no id"},
]


class CatalogTest(unittest.TestCase):
    def test_parse(self):
        cards = {c.code: c for c in catalog.parse(RAW)}
        self.assertEqual(len(cards), 4)
        abandon, jinx, corina = cards["UNL-131"], cards["VEN-168"], cards["SFD-001"]
        self.assertEqual((abandon.main_price, abandon.foil_price, abandon.card_type), (0.46, 3.83, "Spell"))
        self.assertFalse(abandon.is_foil_only)
        self.assertTrue(jinx.is_foil_only)
        self.assertEqual(jinx.main_price, 111.22)
        self.assertAlmostEqual(jinx.change_7d, -1.790002)  # foil-only cards use the foil changes
        self.assertAlmostEqual(jinx.change_1d, 1.0)
        self.assertAlmostEqual(corina.change_pct(7), 2.79 / 1.80)  # +155%, as seen live
        self.assertIsNone(cards["X-1"].change_pct(7))

    def test_versions(self):
        rows = [  # real codes and names from riftbound.gg
            ("OGN-030", "Jinx - Demolitionist", "Rare"), ("OGN-030a", "Jinx - Demolitionist", "Showcase"),
            ("VEN-021A", "Akali - Deadly Weapon", "Showcase"), ("OGN-298", "Last Regular Card", "Common"),
            ("OGN-301", "Jinx - Loose Cannon", "Showcase"), ("OGN-303-STAR", "Ahri - Nine-Tailed Fox", "Showcase"),
            ("VEN-SP3", "Ahri - Inquisitive", "Showcase"),
            ("OGN-066-P", "Ahri - Alluring (Origins Launch Event Promo)", "Rare"),
            ("SFD-R04a", "Body Rune", "Showcase"), ("SFD-R04b", "Body Rune (Spiritforged Nexus Night Promo)", "Showcase"),
            ("UNL-T02", "Bird // Buff", "Common"), ("OGN-279/298", "Fortified Position (Oversized)", "Uncommon"),
            ("SGN - 001/003 - P", "Lillia - Bashful Bloom", "Showcase"),
            ("OGN-263-a", "Teemo - Swift Scout (Worlds Bundle 2025\nPromo)", "Showcase"),
        ]
        raw = [{"id": c, "name": n, "rarity": r, "price": "1"} for c, n, r in rows]
        cards = {c.code: c for c in catalog.parse(raw)}
        expect = {"OGN-030": "Standard", "OGN-030a": "Alternate art", "VEN-021A": "Alternate art",
                  "OGN-301": "Overnumbered", "OGN-303-STAR": "Signature", "VEN-SP3": "Special (SP)",
                  "OGN-066-P": "Promo", "SFD-R04a": "Alternate art", "SFD-R04b": "Promo", "UNL-T02": "Token",
                  "OGN-279/298": "Oversized", "SGN - 001/003 - P": "Promo", "OGN-263-a": "Promo"}
        self.assertEqual({k: cards[k].version for k in expect}, expect)
        self.assertEqual(cards["OGN-066-P"].base_name, "Ahri - Alluring")
        self.assertEqual(cards["OGN-066-P"].detail, "Origins Launch Event Promo")
        self.assertEqual(cards["OGN-263-a"].detail, "Worlds Bundle 2025 Promo")  # line break tidied
        order = sorted(["OGN-031", "OGN-030a", "OGN-030", "OGN-301"], key=lambda k: cards.get(k, cards["OGN-030"]).number_key()
                       if k in cards else (k,))
        self.assertEqual(sorted([cards["OGN-301"], cards["OGN-030a"], cards["OGN-030"]], key=lambda c: c.number_key()),
                         [cards["OGN-030"], cards["OGN-030a"], cards["OGN-301"]])

    def test_save_load_history(self):
        db = CardDatabase(":memory:")
        cards = catalog.parse(RAW)
        catalog.save(db, cards, day="2026-09-26")
        cards[0].price = 0.50
        catalog.save(db, cards, day="2026-09-27")
        loaded = {c.code: c for c in catalog.load(db)}
        self.assertEqual(len(loaded), 4)
        self.assertEqual(catalog.history(db, loaded["UNL-131"]), [("2026-09-26", 0.46), ("2026-09-27", 0.50)])
        self.assertEqual(catalog.history(db, loaded["VEN-168"]), [("2026-09-26", 111.22), ("2026-09-27", 111.22)])
        self.assertEqual(catalog.history(db, loaded["X-1"]), [])
        self.assertEqual(db.get_setting("catalog_updated"), "2026-09-27")
        db.close()


if __name__ == "__main__":
    unittest.main()
