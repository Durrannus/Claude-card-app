import unittest
from datetime import date, datetime, timezone

from card_logger import topdeck
from card_logger.db import CardDatabase
from card_logger.meta import BATTLEFIELDS, LEGEND, MAIN, RUNES, MetaTracker

TODAY = date(2026, 9, 27)


def ts(iso):
    return int(datetime.fromisoformat(iso + "T15:00:00").replace(tzinfo=timezone.utc).timestamp())


# Shaped like the TopDeck.gg v2 bulk tournaments response (see its OpenAPI spec).
RESPONSE = [
    {"TID": "rq-la", "tournamentName": "RQ Los Angeles", "startDate": ts("2026-09-25"), "game": "Riftbound",
     "format": "Constructed", "topCut": 32, "standings": [
         {"standing": 1, "name": "Sam", "id": "p1", "wins": 9, "losses": 1, "draws": 0,
          "leader": "Kennen, Heart of the Tempest",
          "deckObj": {"Legend": {"Kennen, Heart of the Tempest": {"id": "OGN-247", "count": 1}},
                      "Mainboard": {"Gust Monk": {"id": "OGN-051", "count": 3},
                                    "Zephyr Sage": {"count": 2, "id": "OGN-044"}},
                      "Runes": {"Chaos Rune": 12}, "Battlefields": {"Minefield": 1}}},
         {"name": "Ana", "id": "p2", "wins": 8, "losses": 2, "draws": 0,   # real API: no "standing" field
          "leader": "Master Yi, Wuju Bladesman",
          "decklist": "~~Legend~~\n1 Master Yi, Wuju Bladesman\n~~Main Deck~~\n3 Gust Monk\n3 Wind Wall"},
         {"standing": 3, "name": "Lee", "id": "p3", "wins": 7, "losses": 3, "draws": 0},   # no list
         {"standing": 4, "name": "Kim", "id": "p4", "wins": 7, "losses": 3, "draws": 0,
          "decklist": "https://piltoverarchive.com/decks/view/abc"},                      # link only
     ]},
    {"TID": "today", "tournamentName": "Still running", "startDate": ts("2026-09-27"), "standings": [
        {"standing": 1, "name": "X", "id": "x", "deckObj": {"Main Deck": {"Gust Monk": 3}}}]},
    {"TID": "trios", "tournamentName": "Trios", "startDate": ts("2026-09-20"), "isTeamEvent": True,
     "standings": [{"standing": 1, "name": "Team"}]},
]


class TopDeckTest(unittest.TestCase):
    def run_fetch(self, known=None):
        sent = []

        def post(body, key):
            sent.append((body, key))
            return RESPONSE
        result = topdeck.fetch("key123", 30, 8, set() if known is None else known, post=post, today=TODAY)
        return result, sent

    def test_request(self):
        _, sent = self.run_fetch()
        body, key = sent[0]
        self.assertEqual(key, "key123")
        self.assertEqual((body["game"], body["format"], body["last"], body["participantMin"]),
                         ("Riftbound", "Constructed", 30, 8))
        self.assertIn("id", body["columns"])
        self.assertIn("decklist", body["columns"])

    def test_decks(self):
        result, _ = self.run_fetch()
        self.assertEqual(result.tournaments, 1)          # today's and team events skipped
        self.assertEqual(result.without_lists, 2)
        sam, ana = result.decks
        self.assertEqual((sam.event, sam.date, sam.players, sam.placement), ("RQ Los Angeles", "2026-09-25", 4, 1))
        self.assertEqual((sam.wins, sam.losses, sam.ties), (9, 1, 0))
        self.assertEqual(sam.legend, "Kennen, Heart of the Tempest")
        self.assertEqual({(c.section, c.name, c.quantity) for c in sam.cards}, {
            (LEGEND, "Kennen, Heart of the Tempest", 1), (MAIN, "Gust Monk", 3), (MAIN, "Zephyr Sage", 2),
            (RUNES, "Chaos Rune", 12), (BATTLEFIELDS, "Minefield", 1)})
        self.assertEqual(ana.legend, "Master Yi, Wuju Bladesman")  # from the text decklist
        self.assertEqual(ana.placement, 2)  # from its position in the standings
        self.assertEqual({(c.section, c.name) for c in ana.cards},
                         {(LEGEND, "Master Yi, Wuju Bladesman"), (MAIN, "Gust Monk"), (MAIN, "Wind Wall")})
        self.assertIn("TopDeck.gg", sam.notes)

    def test_save_and_reimport(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        result, _ = self.run_fetch()
        self.assertEqual(topdeck.save(meta, result), 2)
        again, _ = self.run_fetch(known=meta.known_sources("topdeck:"))
        self.assertEqual(again.decks, [])
        stored = meta.get_deck(next(d.id for d in meta.decks() if d.player == "Sam"))
        self.assertEqual((stored.wins, stored.losses, stored.players), (9, 1, 4))
        db.close()

    def test_needs_key(self):
        with self.assertRaisesRegex(topdeck.TopDeckError, "API key"):
            topdeck.fetch("  ", 30, 8, set(), post=lambda b, k: [])

    def test_unexpected_response(self):
        with self.assertRaises(topdeck.TopDeckError):
            topdeck.fetch("k", 30, 8, set(), post=lambda b, k: {"error": "nope"}, today=TODAY)


if __name__ == "__main__":
    unittest.main()
