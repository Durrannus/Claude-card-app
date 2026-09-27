import sqlite3
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


class WishlistTest(unittest.TestCase):
    def setUp(self):
        self.db = CardDatabase(":memory:")

    def tearDown(self):
        self.db.close()

    def test_wishlist_kept_separate(self):
        self.db.add(Card(name="Owned", value=5))
        self.db.add(Card(name="Wanted", value=100, quantity=2, wishlist=True))
        self.assertEqual([c.name for c in self.db.search()], ["Owned"])
        self.assertEqual([c.name for c in self.db.search(wishlist=True)], ["Wanted"])
        self.assertEqual(len(self.db.search(wishlist=None)), 2)
        self.assertEqual(self.db.stats()["total_value"], 5)
        self.assertEqual(self.db.stats(wishlist=True)["total_value"], 200)

    def test_wishlist_in_csv(self):
        self.db.add(Card(name="Wanted", wishlist=True))
        self.db.add(Card(name="Owned"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            self.assertEqual(self.db.export_csv(path), 2)
            other = CardDatabase(":memory:")
            other.import_csv(path)
            self.assertEqual([c.name for c in other.search(wishlist=True)], ["Wanted"])
            other.close()


class PhotoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = CardDatabase(self.dir / "cards.db")
        self.photo = self.dir / "my photo.png"
        self.photo.write_bytes(b"not really a png")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_photo_copied_into_image_folder(self):
        card = Card(name="Pikachu")
        self.db.add(card)
        self.db.set_image(card, self.photo)
        stored = Path(self.db.get(card.id).image_path)
        self.assertEqual(stored.parent, self.dir / "images")
        self.assertEqual(stored.read_bytes(), b"not really a png")
        self.assertTrue(self.photo.exists())  # original untouched

    def test_replace_remove_and_delete_clean_up(self):
        card = Card(name="Pikachu")
        self.db.add(card)
        self.db.set_image(card, self.photo)
        first = Path(card.image_path)
        other = self.dir / "other.jpg"
        other.write_bytes(b"jpg")
        self.db.set_image(card, other)
        self.assertFalse(first.exists())
        second = Path(card.image_path)
        self.db.set_image(card, None)
        self.assertFalse(second.exists())
        self.assertEqual(self.db.get(card.id).image_path, "")

        self.db.set_image(card, self.photo)
        third = Path(card.image_path)
        self.db.delete(card.id)
        self.assertFalse(third.exists())
        self.assertTrue(self.photo.exists())

    def test_rejects_non_image(self):
        card = Card(name="Pikachu")
        self.db.add(card)
        doc = self.dir / "notes.txt"
        doc.write_text("hi")
        with self.assertRaises(ValueError):
            self.db.set_image(card, doc)


class UpgradeTest(unittest.TestCase):
    def test_opens_database_from_first_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.db"
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE cards (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                "game TEXT NOT NULL DEFAULT '', set_name TEXT NOT NULL DEFAULT '', "
                "number TEXT NOT NULL DEFAULT '', rarity TEXT NOT NULL DEFAULT '', "
                "condition TEXT NOT NULL DEFAULT '', quantity INTEGER NOT NULL DEFAULT 1, "
                "value REAL NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '', "
                "date_added TEXT NOT NULL DEFAULT '')"
            )
            conn.execute("INSERT INTO cards (name) VALUES ('Old card')")
            conn.commit()
            conn.close()

            db = CardDatabase(path)
            card = db.search()[0]
            self.assertEqual((card.name, card.wishlist, card.image_path), ("Old card", False, ""))
            db.add(Card(name="New", wishlist=True))
            self.assertEqual(len(db.search(wishlist=True)), 1)
            db.close()


class SoldPriceTest(unittest.TestCase):
    def test_sold_prices_per_printing(self):
        db = CardDatabase(":memory:")
        db.add_sold_price("Jinx - Rebel", 3.0, day="2026-09-20")
        db.add_sold_price("Jinx - Rebel", 5.0, day="2026-09-21", code="OGN-202a")
        db.add_sold_price("Jinx, Rebel", 7.0, day="2026-09-22", code="OGN-202A")
        self.assertEqual(len(db.sold_prices("Jinx - Rebel")), 3)  # every printing
        self.assertEqual([r["price"] for r in db.sold_prices("Jinx - Rebel", "ogn-202a")], [7.0, 5.0])
        self.assertEqual([r["price"] for r in db.sold_prices("Jinx - Rebel", "")], [3.0])
        self.assertEqual(list(db.sold_prices_by_code()), ["OGN-202A"])
        self.assertEqual(len(db.sold_prices_by_code()["OGN-202A"]), 2)
        db.close()

    def test_adds_code_to_older_sold_prices_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE sold_prices (id INTEGER PRIMARY KEY AUTOINCREMENT, name_key TEXT NOT NULL, "
                         "name TEXT NOT NULL, day TEXT NOT NULL, price REAL NOT NULL, note TEXT NOT NULL DEFAULT '')")
            conn.execute("INSERT INTO sold_prices (name_key, name, day, price) VALUES ('a', 'A', '2026-09-01', 2)")
            conn.commit()
            conn.close()
            db = CardDatabase(path)
            self.assertEqual(db.sold_prices("A")[0]["code"], "")
            db.close()


if __name__ == "__main__":
    unittest.main()
