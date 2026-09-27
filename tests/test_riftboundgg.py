import json
import unittest
import urllib.parse
from datetime import date

from card_logger import riftboundgg as rg
from card_logger.db import CardDatabase
from card_logger.meta import BATTLEFIELDS, LEGEND, MAIN, RUNES, MetaTracker

TODAY = date(2026, 9, 27)


def ts(iso):  # unix timestamp for noon UTC on a day
    from datetime import datetime, timezone
    return str(int(datetime.fromisoformat(iso + "T12:00:00").replace(tzinfo=timezone.utc).timestamp()))


# Shapes copied from real api.dotgg.gg responses (trimmed).
CARDS = [
    {"id": "OGN-269", "name": "Sett - The Boss", "type": ["Legend"], "supertype": ""},
    {"id": "OGN-126", "name": "Body Rune", "type": ["Rune"], "supertype": "Basic"},
    {"id": "OGN-214", "name": "Order Rune", "type": ["Rune"], "supertype": "Basic"},
    {"id": "OGN-280", "name": "Grove of the God-Willow", "type": ["Battlefield"], "supertype": ""},
    {"id": "OGN-164", "name": "Sett - Brawler", "type": ["Unit"], "supertype": "Champion"},
    {"id": "OGN-128", "name": "Challenge", "type": ["Spell"], "supertype": ""},
    {"id": "OGN-263a", "name": "Pit Rookie", "type": ["Unit"], "supertype": ""},
    {"id": "OGN-301", "name": "Jinx - Loose Cannon", "type": ["Unit"], "supertype": "Champion"},
]


def deck_codes(extra=None):
    codes = {"OGN-269": "1", "OGN-126": "8", "OGN-214": "4", "OGN-280": "1", "OGN-164": "3",
             "OGN-128": "20", "OGN-263-a": "17", "OGN-301-STAR": "2"}
    codes.update(extra or {})
    return codes


def raw_deck(slug, posted, tournament=None, fingerprint=None, codes=None):
    return {"slug": slug, "humanname": f"eli - {slug}", "date": ts(posted), "fingerprint": fingerprint or slug,
            "is_tournament": "1" if tournament else "0", "deck": codes or deck_codes(), "tournament": tournament}


TOURNAMENTS = [
    {"date": ts("2026-09-26"), "name": "Vendetta Case Tournament", "slug": "vct", "players_count": "37"},
    {"date": ts("2026-09-20"), "name": "Remote Showdown #4", "slug": "rs4", "players_count": "64"},
    {"date": ts("2026-08-01"), "name": "Ancient Cup", "slug": "old", "players_count": "300"},
]
T_DECKS = [
    raw_deck("t1", "2026-09-26", {"place": 1, "tournament_slug": "vct", "tournament_name": "VCT"}),
    raw_deck("t2", "2026-09-26", {"place": "5", "tournament_slug": "rs4"}),
    raw_deck("t3", "2026-09-25", {"place": 2, "tournament_slug": "old"}),       # event too old
    raw_deck("t4", "2026-09-25", {"place": 3, "tournament_slug": "unknown"}),   # event not listed
]
C_DECKS = [
    raw_deck("c1", "2026-09-27", fingerprint="fp1"),
    raw_deck("c1-copy", "2026-09-27", fingerprint="fp1"),                        # a copy: counted once
    raw_deck("c2", "2026-09-26", fingerprint="fp2", codes={"OGN-269": "1", "OGN-128": "3"}),  # unfinished
    raw_deck("c3", "2026-09-26", fingerprint="fp3", codes=deck_codes({"XXX-999": "1"})),
    raw_deck("tflag", "2026-09-26", {"place": 1, "tournament_slug": "vct"}),      # tournament: skipped here
    raw_deck("c-old", "2026-07-01", fingerprint="fp-old"),
]


def fake_api(requested):
    def fetch(url):
        requested.append(url)
        path = url.split("/cgfw/", 1)[1]
        if path.startswith("getcards"):
            return CARDS
        if path.startswith("gettournaments"):
            page = int(path.split("page=")[1])
            return TOURNAMENTS if page == 1 else []
        if path.startswith("getdecks"):
            rq = json.loads(urllib.parse.unquote(path.split("rq=", 1)[1]))
            if rq["page"] > 1:
                return []
            return T_DECKS if rq["getdecks"]["is_tournament"] == 1 else C_DECKS
        raise AssertionError(url)
    return fetch


class RiftboundGGTest(unittest.TestCase):
    def run_fetch(self, known=None, **kw):
        requested = []
        result = rg.fetch(30, set() if known is None else known, fetch=fake_api(requested), today=TODAY, pause=0, **kw)
        return result, requested

    def test_codes_resolve(self):
        cards = rg.load_cards(fake_api([]))
        self.assertEqual(rg.resolve_code("OGN-263-a", cards), ("Pit Rookie", MAIN))
        self.assertEqual(rg.resolve_code("OGN-301-STAR", cards), ("Jinx - Loose Cannon", MAIN))
        self.assertEqual(rg.resolve_code("ogn-301*", cards), ("Jinx - Loose Cannon", MAIN))
        self.assertEqual(rg.resolve_code("OGN-126-P", cards), ("Body Rune", RUNES))
        self.assertEqual(rg.resolve_code("OGN-269", cards), ("Sett - The Boss", LEGEND))
        self.assertIsNone(rg.resolve_code("XXX-999", cards))

    def test_tournament_decks(self):
        result, _ = self.run_fetch(community=False)
        self.assertEqual(result.tournament_decks, 2)
        self.assertEqual(result.skipped_undated, 2)  # old event and unlisted event
        t1, t2 = result.decks
        self.assertEqual((t1.event, t1.date, t1.players, t1.placement, t1.legend),
                         ("Vendetta Case Tournament", "2026-09-26", 37, 1, "Sett - The Boss"))
        self.assertEqual((t2.placement, t2.date), (5, "2026-09-20"))  # dated by the event, not the upload
        self.assertEqual(t1.player, "eli")
        sections = {(c.section, c.name): c.quantity for c in t1.cards}
        self.assertEqual(sections[(LEGEND, "Sett - The Boss")], 1)
        self.assertEqual(sections[(RUNES, "Body Rune")], 8)
        self.assertEqual(sections[(BATTLEFIELDS, "Grove of the God-Willow")], 1)
        self.assertEqual(sections[(MAIN, "Jinx - Loose Cannon")], 2)

    def test_community_decks(self):
        result, _ = self.run_fetch(tournaments=False)
        self.assertEqual(result.community_decks, 2)  # c1 (once), c3; c2 too small; old and tournament skipped
        self.assertEqual(result.skipped_small, 1)
        self.assertEqual(result.unknown_codes, 1)
        self.assertEqual({d.source_id for d in result.decks}, {"riftboundgg:c:fp1", "riftboundgg:c:fp3"})
        self.assertTrue(all(d.event == rg.COMMUNITY_EVENT and d.placement is None for d in result.decks))

    def test_save_and_reimport(self):
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        result, _ = self.run_fetch()
        self.assertEqual(rg.save(meta, result), 4)
        again, _ = self.run_fetch(known=meta.known_sources("riftboundgg:"))
        self.assertEqual(again.decks, [])
        self.assertEqual(rg.save(meta, again), 0)
        self.assertEqual(len(meta.decks()), 4)
        db.close()

    def test_bad_responses(self):
        with self.assertRaises(rg.FetchError):
            rg.load_cards(lambda url: {})


if __name__ == "__main__":
    unittest.main()
