import json
import unittest
from datetime import date

from card_logger import riotnews
from card_logger.db import CardDatabase
from card_logger.meta import BATTLEFIELDS, CHAMPION, LEGEND, MAIN, RUNES, SIDEBOARD, MetaTracker

# Trimmed from Riot's "Los Angeles' Top Decks" (current layout) and an older
# article's layout ("<strong>Legend</strong>:").
NEW_DECK = (
    '<table><tbody><tr><td colspan="2"><h3>DSG Prismaticism</h3><p><strong>Legend Rank:</strong> #1/92&nbsp;<br>'
    '<strong>Overall Ranking:</strong> #1</p></td></tr><tr><td><p><strong>Legend:</strong>&nbsp;<br>'
    '1 Rengar, Pridestalker</p><p><strong>Champion:</strong>&nbsp;<br>1 Rengar, Trophy Hunter</p>'
    '<p><strong>Main Deck:</strong>&nbsp;<br>3 Baited Hook&nbsp;<br>3 Hidden Blade</p></td><td>'
    '<p><strong>Battlefields:</strong>&nbsp;<br>1 Seat of Power</p><p><strong>Rune Pool:</strong>&nbsp;<br>'
    '6 Fury Rune&nbsp;<br>6 Chaos Rune</p><p><strong>Sideboard:</strong>&nbsp;<br>2 Zaun Punk</p></td></tr>'
    '</tbody></table>'
)
OLD_DECK = (
    '<table><tbody><tr><td colspan="2"><h3>Xeno</h3><p><strong>Legend Rank</strong>: #1/193<br>'
    '<strong>Overall Ranking</strong>: #02</p></td></tr><tr><td><p><strong>Legend</strong>:<br>'
    '1 Irelia, Blade Dancer</p><p><strong>Main Deck</strong>:<br>3 Defy<br>3 Discipline</p></td></tr></tbody></table>'
)
FIELD = ('<table><tbody><tr><td><strong>Legend</strong></td><td><strong># Played</strong></td>'
         '<td><strong>% of Field</strong></td></tr><tr><td>Irelia</td><td>193</td><td>9.8%</td></tr>'
         '<tr><td>Rengar</td><td>92</td><td>4.7%</td></tr></tbody></table>')


def article(title, published, body):
    data = {"props": {"pageProps": {"page": {"title": title, "displayedPublishDate": published, "blades": [
        {"type": "articleMasthead"}, {"type": "articleRichText", "richText": {"type": "html", "body": body}}]}}}}
    return f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></html>'


LA = article("Los Angeles’ Top Decks", "2026-09-30T16:00:00.000Z",
             f"<h1>Best-of Decks</h1>{FIELD}<div>{OLD_DECK}</div><h1>Top 8 Decks</h1>{NEW_DECK}{OLD_DECK}")
OLD = article("From Garen to Annie: RQ Houston's Top Decks", "2025-12-11T16:00:00.000Z", NEW_DECK)
NEWS = ('<a href="/en-us/news/organizedplay/los-angeles-top-decks/">LA</a>'
        '<a href="/en-us/news/announcements/from-garen-to-annie-rq-houstons-top-decks">Houston</a>'
        '<a href="/en-us/news/rules-and-releases/deckbuilding-primer">Primer</a>'
        '<a href="/en-us/news/organizedplay/los-angeles-top-decks">again</a>')
PAGES = {
    riotnews.NEWS: NEWS,
    "https://playriftbound.com/en-us/news/organizedplay/los-angeles-top-decks/": LA,
    "https://playriftbound.com/en-us/news/announcements/from-garen-to-annie-rq-houstons-top-decks/": OLD,
}


class RiotNewsTest(unittest.TestCase):
    def test_article_links(self):
        self.assertEqual(riotnews.article_links(NEWS), [
            "https://playriftbound.com/en-us/news/organizedplay/los-angeles-top-decks/",
            "https://playriftbound.com/en-us/news/announcements/from-garen-to-annie-rq-houstons-top-decks/"])

    def test_parse_article(self):
        a = riotnews.parse_article(LA, "https://x/los-angeles-top-decks/")
        self.assertEqual((a.event, a.published, a.day, a.players),
                         ("Regional Qualifier Los Angeles", "2026-09-30", "2026-09-27", 285))
        self.assertEqual([(d.player, d.placement) for d in a.decks], [("Xeno", 2), ("DSG Prismaticism", 1)])
        winner = a.decks[1]
        self.assertEqual(winner.legend, "Rengar, Pridestalker")
        counts = {s: sum(c.quantity for c in winner.cards if c.section == s)
                  for s in (LEGEND, CHAMPION, MAIN, BATTLEFIELDS, RUNES, SIDEBOARD)}
        self.assertEqual(counts, {LEGEND: 1, CHAMPION: 1, MAIN: 6, BATTLEFIELDS: 1, RUNES: 12, SIDEBOARD: 2})
        self.assertEqual((winner.date, winner.players, winner.event), ("2026-09-27", 285, a.event))
        self.assertTrue(winner.source_id.startswith("riot:los-angeles-top-decks:"))
        self.assertEqual(riotnews.parse_article(OLD, "u").event, "Regional Qualifier Houston")

    def test_fetch_and_save(self):
        fetch = PAGES.__getitem__
        r = riotnews.fetch(30, set(), fetch=fetch, today=date(2026, 10, 1), pause=0)
        self.assertEqual([a.event for a in r.articles], ["Regional Qualifier Los Angeles"])  # Houston is too old
        db = CardDatabase(":memory:")
        meta = MetaTracker(db)
        self.assertEqual(riotnews.save(meta, r), 2)
        again = riotnews.fetch(30, meta.known_sources("riot:"), fetch=fetch, today=date(2026, 10, 1), pause=0)
        self.assertEqual(again.decks, [])  # already imported
        self.assertEqual(meta.decks(top=1)[0].player, "DSG Prismaticism")

    def test_layout_change_is_reported(self):
        with self.assertRaises(riotnews.FetchError):
            riotnews.parse_article("<html>no data</html>", "u")


if __name__ == "__main__":
    unittest.main()
