"""Check that every live data source works: python -m card_logger --check

Tries each service the app uses with a real request, runs the app's own
parsing on the answer, prints PASS/FAIL for each, and saves a report with a
short sample of every response. If something fails, that report shows
exactly what the service sent, so the parsing can be fixed.
"""

import json
import traceback
from datetime import datetime
from pathlib import Path

from . import limitless, pricing
from .db import Card, DEFAULT_DB_PATH

SAMPLE_CHARS = 1500


class Recorder:
    """Wraps the app's fetch function, keeping a sample of every response."""

    def __init__(self):
        self.samples: list[tuple[str, str]] = []

    def fetch(self, url: str):
        data = pricing._get_json(url)
        self.samples.append((url, json.dumps(data, ensure_ascii=False)[:SAMPLE_CHARS]))
        return data


def check_riftbound_prices(rec: Recorder) -> str:
    groups = rec.fetch(f"{pricing.TCGCSV}/groups").get("results", [])
    if not groups:
        raise AssertionError("No Riftbound sets returned")
    newest = sorted(groups, key=lambda g: g.get("publishedOn") or "", reverse=True)[0]
    gid = newest["groupId"]
    products = rec.fetch(f"{pricing.TCGCSV}/{gid}/products").get("results", [])
    prices = rec.fetch(f"{pricing.TCGCSV}/{gid}/prices").get("results", [])
    market = pricing.parse_riftbound_market([(products, prices)])
    if not market:
        raise AssertionError(f"Set '{newest.get('name')}' returned {len(products)} products but no card prices "
                             "could be read")
    name, price = next(iter(market.values()))
    result = pricing.lookup_price(Card(name=name, game="Riftbound", set_name=newest.get("name", "")),
                                  fetch=rec.fetch)
    return (f"{len(groups)} sets; newest '{newest.get('name')}' has {len(market)} priced cards. "
            f"Looked up '{name}': ${result.price:,.2f} ({result.matched})")


def check_limitless(rec: Recorder) -> str:
    game_id = limitless.find_game_id(fetch=rec.fetch)
    tournaments = rec.fetch(f"{limitless.API}/tournaments?game={game_id}&limit=10")
    if not isinstance(tournaments, list) or not tournaments:
        raise AssertionError(f"Game id '{game_id}' found but no tournaments listed")
    decks, checked = 0, 0
    for t in tournaments:
        standings = rec.fetch(f"{limitless.API}/tournaments/{t['id']}/standings")
        checked += 1
        if isinstance(standings, list):
            event = limitless.Event(str(t["id"]), t.get("name", ""), str(t.get("date", ""))[:10], t.get("players") or 0)
            decks = sum(1 for s in standings if limitless.standing_to_deck(event, s))
            if decks:
                deck = next(limitless.standing_to_deck(event, s) for s in standings
                            if limitless.standing_to_deck(event, s))
                sections = sorted({c.section for c in deck.cards})
                return (f"Riftbound game id '{game_id}'. '{t.get('name')}' has {decks} decklists; first one: "
                        f"legend '{deck.legend}', {len(deck.cards)} cards in {', '.join(sections)}, "
                        f"record {deck.wins}-{deck.losses}")
        if checked >= 5:
            break
    raise AssertionError(f"Checked {checked} recent tournaments but read no decklists from them "
                         "(they may be private, or the decklist format differs; see samples)")


def check_other(rec: Recorder, game: str, name: str) -> str:
    result = pricing.lookup_price(Card(name=name, game=game), fetch=rec.fetch)
    return f"'{name}': ${result.price:,.2f} ({result.matched})"


CHECKS = [
    ("Riftbound prices (TCGCSV)", check_riftbound_prices),
    ("Riftbound tournaments (Limitless)", check_limitless),
    ("Magic prices (Scryfall)", lambda rec: check_other(rec, "Magic", "Lightning Bolt")),
    ("Pokémon prices (Pokémon TCG API)", lambda rec: check_other(rec, "Pokémon", "Pikachu")),
    ("Yu-Gi-Oh! prices (YGOPRODeck)", lambda rec: check_other(rec, "Yu-Gi-Oh!", "Dark Magician")),
]


def run(report_path: Path | None = None) -> bool:
    report_path = report_path or Path(DEFAULT_DB_PATH).parent / "connection-check.txt"
    lines = [f"Card Collection Logger connection check, {datetime.now():%Y-%m-%d %H:%M}", ""]
    all_ok = True
    print("Checking live data sources…\n")
    for label, check in CHECKS:
        rec = Recorder()
        try:
            detail = check(rec)
            status = "PASS"
        except Exception as e:  # report every failure, keep checking the rest
            all_ok = False
            status, detail = "FAIL", f"{type(e).__name__}: {e}"
            lines.append(traceback.format_exc())
        print(f"  [{status}] {label}\n         {detail}\n")
        lines += [f"[{status}] {label}", f"    {detail}", "    Responses:"]
        for url, sample in rec.samples:
            lines += [f"      {url}", f"        {sample}"]
        lines.append("")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print("All live data sources work." if all_ok else "Some checks failed.")
    print(f"Full report with response samples saved to:\n  {report_path}")
    return all_ok
