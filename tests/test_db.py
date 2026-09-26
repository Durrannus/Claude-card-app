import tempfile
import unittest
from pathlib import Path

from card_logger.db import Card, CardDatabase


class CardDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.db = CardDatabase(":memory:")

    def tearDown(self):
        self.db.close()

    def test_add_and_get(self):
        card_id = self.db.add(Card(name="Charizard", game="Pokemon", quantity=2, value=10.5))
        card = self.db.get(card_id)
        self.assertEqual(card.name, "Charizard")
        self.assertEqual(card.quantity, 2)
        self.assertTrue(card.date_added)

    def test_name_required(self):
        with self.assertRaises(ValueError):
            self.db.add(Card(name="   "))

    def test_negative_values_rejected(self):
        with self.assertRaises(ValueError):
            self.db.add(Card(name="X", quantity=-1))
        with self.assertRaises(ValueError):
            self.db.add(Card(name="X", value=-1))

    def test_update_and_delete(self):
        card_id = self.db.add(Card(name="Black Lotus", game="Magic"))
        card = self.db.get(card_id)
        card.condition = "Played"
        self.db.update(card)
        self.assertEqual(self.db.get(card_id).condition, "Played")
        self.db.delete(card_id)
        self.assertIsNone(self.db.get(card_id))

    def test_search_and_games(self):
        self.db.add(Card(name="Pikachu", game="Pokemon", set_name="Base Set"))
        self.db.add(Card(name="Charizard", game="Pokemon", set_name="Base Set"))
        self.db.add(Card(name="Lightning Bolt", game="Magic", notes="Alpha"))
        self.assertEqual(self.db.games(), ["Magic", "Pokemon"])
        self.assertEqual(len(self.db.search("base")), 2)
        self.assertEqual([c.name for c in self.db.search("alpha")], ["Lightning Bolt"])
        self.assertEqual(len(self.db.search(game="Pokemon")), 2)
        self.assertEqual([c.name for c in self.db.search("pika", "Pokemon")], ["Pikachu"])

    def test_stats(self):
        self.db.add(Card(name="A", quantity=3, value=2.0))
        self.db.add(Card(name="B", quantity=1, value=0.25))
        self.assertEqual(
            self.db.stats(), {"entries": 2, "total_cards": 4, "total_value": 6.25}
        )

    def test_csv_round_trip(self):
        self.db.add(Card(name="Pikachu", game="Pokemon", quantity=4, value=1.5, notes="holo, 1st ed"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            self.assertEqual(self.db.export_csv(path), 1)
            other = CardDatabase(":memory:")
            self.assertEqual(other.import_csv(path), 1)
            card = other.search()[0]
            self.assertEqual((card.name, card.quantity, card.value, card.notes),
                             ("Pikachu", 4, 1.5, "holo, 1st ed"))
            other.close()

    def test_import_minimal_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            path.write_text("name,quantity,extra\nMewtwo,,ignored\n,5,\n", encoding="utf-8")
            self.assertEqual(self.db.import_csv(path), 1)
            self.assertEqual(self.db.search()[0].quantity, 1)

    def test_import_bad_number_reports_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            path.write_text("name,quantity\nMewtwo,lots\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Line 2"):
                self.db.import_csv(path)

    def test_persists_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub" / "cards.db"
            db = CardDatabase(path)
            db.add(Card(name="Saved"))
            db.close()
            db = CardDatabase(path)
            self.assertEqual(db.search()[0].name, "Saved")
            db.close()


if __name__ == "__main__":
    unittest.main()
