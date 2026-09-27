import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from card_logger import catalog, currency, phone_api, phone_build
from card_logger.db import Card, CardDatabase
from card_logger.meta import MetaTracker, parse_decklist
from tests.test_catalog import RAW

DECK = """Legend:
1 Jinx, Loose Cannon
Main Deck:
3 Abandon
3 Corina Veraza
"""


def make_market(folder: Path) -> Path:
    """A shared market database like the daily build makes, written out as a site."""
    db = CardDatabase(folder / "shared.db")
    meta = MetaTracker(db)
    catalog.save(db, catalog.parse(RAW), day=(date.today() - timedelta(days=3)).isoformat())
    catalog.save(db, catalog.parse(RAW))
    db.set_setting("fx_rates", json.dumps({"rates": {"USD": 1, "GBP": 0.75, "EUR": 0.875}, "day": "2026-09-25"}))
    db.record_price("Abandon", 0.40, game="Riftbound")
    db.add(Card(name="Should not travel", value=1))  # personal rows never reach the phone
    for i in range(30):
        deck = parse_decklist(DECK)
        deck.source_id = f"test:{i}"
        deck.event = "Test Open"
        deck.placement = i + 1
        deck.date = (date.today() - timedelta(days=i % 20)).isoformat()
        meta.add_deck(deck)
    site = folder / "_site"
    phone_build.write_site(db, site, [])
    db.close()
    return site


class PhoneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.site = make_market(Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.user = Path(self.tmp.name) / f"user_{self._testMethodName}.db"
        self.info = self.api("open_db", path=str(self.user), market_path=str(self.site / "data" / "market.db"),
                             built="build-1")

    def tearDown(self):
        phone_api.DB.close()

    def api(self, _fn, **args):
        out = json.loads(phone_api.call(_fn, json.dumps(args)))
        if "error" in out:
            raise AssertionError(out["error"])
        return out["ok"]

    def test_site_contents(self):
        files = json.loads((self.site / "py" / "files.json").read_text())
        self.assertIn("card_logger/phone_api.py", files)
        self.assertTrue((self.site / "index.html").exists())
        conn = sqlite3.connect(self.site / "data" / "market.db")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("cards", tables)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM decks").fetchone()[0], 30)
        conn.close()
        self.assertEqual(json.loads((self.site / "data" / "info.json").read_text())["decks"], 30)

    def test_market_merged_in_pounds(self):
        self.assertEqual((self.info["currency"], self.info["printings"], self.info["decks"]), ("GBP", 4, 30))
        data = self.api("catalog_list", search="jinx")
        self.assertEqual([r["code"] for r in data["rows"]], ["VEN-168"])
        self.assertEqual(data["rows"][0]["price"], "£83.41 F")  # $111.22 at 0.75
        card = self.api("catalog_card", code="SFD-001")
        self.assertEqual(card["price"], "£3.44")
        self.assertEqual(len(card["history"]), 2)
        self.assertIn("Played in 100% of decklists", card["facts"])

    def test_collection_and_signals(self):
        form = self.api("catalog_form", code="SFD-001")
        form.update(quantity="2", paid="2.00", value_original=form["value"])
        card_id = self.api("save_card", fields=form)
        rows = self.api("collection")["rows"]
        self.assertEqual((rows[0]["id"], rows[0]["value"], rows[0]["number"]), (card_id, "£3.44", "SFD-001"))
        mine = self.api("signals", view="mine")
        self.assertEqual(mine["rows"][0]["name"], "Corina Veraza")
        self.assertEqual(mine["rows"][0]["profit"], "+£2.88")  # (3.4425 - 2.00) * 2
        self.assertEqual(self.api("card", id=card_id)["paid"], "2.00")

    def test_meta_decks_and_merge_keeps_yours(self):
        meta = self.api("meta")
        self.assertEqual(meta["tiles"]["decks"], "30")
        self.assertEqual(meta["usage"][0]["share"], "100%")
        mine = self.api("add_deck", text=DECK, player="Me", placement="2")
        self.assertEqual(self.api("deck", id=mine)["sections"][0]["name"], "Legend")
        self.assertIn("Added", self.api("add_missing", names=["Abandon"]))
        # The next day's download replaces imported decks but keeps yours.
        phone_api.DB.close()
        self.api("open_db", path=str(self.user), market_path=str(self.site / "data" / "market.db"), built="build-2")
        decks = self.api("meta")["decks"]
        self.assertEqual(len(decks), 31)
        self.assertEqual(sum(1 for d in decks if d["mine"]), 1)

    def test_sold_prices_insight_and_currency(self):
        self.api("sold_add", name="Corina Veraza", price="£4.20", code="SFD-001")
        self.assertEqual(self.api("catalog_list", search="corina")["rows"][0]["ebay"], "£4.20")
        report = self.api("insight_report")
        self.assertEqual(report["tiles"]["decks"], "30")
        self.api("set_currency", code="$ USD")
        self.assertEqual(self.api("sold", name="Corina Veraza", code="SFD-001")["rows"][0]["price"], "$5.60")
        self.api("set_currency", code="£ GBP")

    def test_csv_round_trip(self):
        self.api("save_card", fields={"name": "Abandon", "game": "Riftbound", "quantity": "3", "value": "0.35"})
        text = self.api("export_csv")
        self.assertIn("Abandon", text)
        self.assertEqual(self.api("import_csv", text=text), 1)
        self.assertEqual(len(self.api("collection")["rows"]), 2)

    def test_errors_come_back_as_messages(self):
        out = json.loads(phone_api.call("catalog_card", json.dumps({"code": "NOPE-1"})))
        self.assertIn("isn't in the price list", out["error"])


if __name__ == "__main__":
    unittest.main()
