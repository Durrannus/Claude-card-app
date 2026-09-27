import unittest
from datetime import date
from unittest import mock

from card_logger import limitless
from card_logger.db import CardDatabase
from card_logger.meta import BATTLEFIELDS, CHAMPION, LEGEND, MAIN, RUNES, MetaTracker

TODAY = date(2026, 9, 27)

TOURNAMENTS = [  # newest first, as Limitless lists them
    {"id": "t-today", "name": "Running now", "date": "2026-09-27T10:00:00.000Z", "players": 40},
    {"id": "t1", "name": "Weekly #12", "date": "2026-09-25T18:00:00.000Z", "players": 24},
    {"id": "t-small", "name": "Tiny", "date": "2026-09-20T18:00:00.000Z", "players": 4},
    {"id": "t2", "name": "Store Showdown", "date": "2026-09-10T18:00:00.000Z", "players": 16},
    {"id": "t-old", "name": "Ancient", "date": "2026-07-01T18:00:00.000Z", "players": 64},
]

KENNEN = {
    "legend": [{"count": 1, "name": "Kennen, Heart of the Tempest"}],
    "champion": [{"count": 1, "name": "Kennen, Storm of Shuriken"}],
    "main": [{"count": 3, "name": "Gust Monk"}, {"count": 2, "name": "Zephyr Sage"}],
    "runes": [{"count": 12, "name": "Chaos Rune"}],
    "battlefields": [{"count": 1, "name": "Minefield"}],
}
STANDINGS = {
    "t1": [
        {"player": "sam", "name": "Sam", "placing": 1, "decklist": KENNEN, "deck": {"name": "Kennen"}},
        {"player": "ana", "name": "Ana", "placing": 2, "decklist": None, "deck": {"name": "Master Yi"}},
        # A flat list that names its own sections, and no legend section.
        {"player": "lee", "name": "Lee", "placing": 3, "deck": {"name": "Irelia, Blade Dancer"},
         "decklist": [{"quantity": 3, "name": "Gust Monk", "category": "Main Deck"},
                      {"quantity": 6, "name": "Calm Rune", "category": "Rune"}]},
    ],
    "t2": [{"player": "kim", "name": "Kim", "placing": 5, "decklist": {"Main Deck": {"Gust Monk": 3}}}],
}


def fake_fetch(requested, games=True):
    def fetch(url):
        requested.append(url)
        path = url.split("/api", 1)[1]
        if path == "/games":
            return [{"id": "PTCG", "name": "Pokémon TCG"}, {"id": "RB", "name": "Riftbound"}] if games else {}
        if path.startswith("/tournaments?"):
            if "game=RB" not in path:
                return []
            page = int(path.split("page=")[1]) if "page=" in path else 1
            return TOURNAMENTS[(page - 1) * 50: page * 50] if "limit=1&" not in path + "&" else TOURNAMENTS[:1]
        if path.endswith("/standings"):
            return STANDINGS.get(path.split("/")[2], [])
        return {}
    return fetch


@mock.patch.object(limitless, "PAUSE", 0)
class LimitlessTest(unittest.TestCase):
    def test_game_id_from_games_list(self):
        self.assertEqual(limitless.find_game_id(fake_fetch([])), "RB")

    def test_game_id_guessed_when_no_games_list(self):
        self.assertEqual(limitless.find_game_id(fake_fetch([], games=False)), "RB")

    def test_fetch_filters_and_parses(self):
        requested = []
        events = limitless.fetch_events("RB", days=30, min_players=8, already_checked=set(),
                                        fetch=fake_fetch(requested), today=TODAY)
        self.assertEqual([e.tournament_id for e in events], ["t1", "t2"])  # not today's, tiny or old ones
        t1 = events[0]
        self.assertEqual((t1.date, t1.players), ("2026-09-25", 24))
        self.assertEqual([d.player for d in t1.decks], ["Sam", "Lee"])  # Ana's list isn't public
        sam = t1.decks[0]
        self.assertEqual((sam.legend, sam.placement, sam.event), ("Kennen, Heart of the Tempest", 1, "Weekly #12"))
        self.assertEqual({(c.section, c.name, c.quantity) for c in sam.cards}, {
            (LEGEND, "Kennen, Heart of the Tempest", 1), (CHAMPION, "Kennen, Storm of Shuriken", 1),
            (MAIN, "Gust Monk", 3), (MAIN, "Zephyr Sage", 2), (RUNES, "Chaos Rune", 12),
            (BATTLEFIELDS, "Minefield", 1)})
        lee = t1.decks[1]
        self.assertEqual(lee.legend, "Irelia, Blade Dancer")  # from the archetype name
        self.assertEqual({(c.section, c.name) for c in lee.cards}, {(MAIN, "Gust Monk"), (RUNES, "Calm Rune")})
        self.assertEqual([(c.name, c.quantity) for c in events[1].decks[0].cards], [("Gust Monk", 3)])

    def test_skips_checked_tournaments(self):
        requested = []
        events = limitless.fetch_events("RB", 30, 8, {"t1"}, fetch=fake_fetch(requested), today=TODAY)
        self.assertEqual([e.tournament_id for e in events], ["t2"])
        self.assertFalse(any("/t1/standings" in u for u in requested))

    def test_save_is_idempotent(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        events = limitless.fetch_events("RB", 30, 8, set(), fetch=fake_fetch([]), today=TODAY)
        self.assertEqual(limitless.save_events(meta, events), (3, 2))
        self.assertEqual(limitless.save_events(meta, events), (0, 2))
        self.assertEqual(len(meta.decks()), 3)
        self.assertEqual(limitless.checked_tournaments(meta), {"t1", "t2"})
        usage = {u.name: u for u in meta.card_usage()}
        self.assertEqual(usage["Gust Monk"].decks, 3)
        db.close()

    def test_bad_entries_ignored(self):
        cards = limitless.parse_limitless_decklist({"main": [{"count": "x", "name": "A"}, {"count": 2, "name": ""},
                                                             "junk", {"count": 1, "name": "Ok"}]})
        self.assertEqual([(c.name, c.quantity) for c in cards], [("Ok", 1)])
        self.assertEqual(limitless.parse_limitless_decklist(None), [])


if __name__ == "__main__":
    unittest.main()
