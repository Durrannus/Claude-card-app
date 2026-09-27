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

from datetime import date

import os

from . import currency, pricing, riftboundgg, topdeck
from .db import Card, CardDatabase, DEFAULT_DB_PATH

SAMPLE_CHARS = 1500


class Skipped(Exception):
    """The check can't run yet (e.g. no API key); not a failure."""


class Recorder:
    """Wraps the app's fetch function, keeping a sample of every response."""

    def __init__(self):
        self.samples: list[tuple[str, str]] = []

    def fetch(self, url: str):
        data = riftboundgg.http_json(url) if url.startswith(riftboundgg.API) else pricing._get_json(url)
        self.samples.append((url, json.dumps(data, ensure_ascii=False)[:SAMPLE_CHARS]))
        return data


def check_riftbound_prices(rec: Recorder) -> str:
    groups = rec.fetch(f"{pricing.TCGCSV}/groups").get("results", [])
    if not groups:
        raise AssertionError("No Riftbound sets returned")
    # Try the newest released sets first; some groups (future releases, bundles)
    # only list sealed product.
    released = [g for g in groups if (g.get("publishedOn") or "")[:10] <= date.today().isoformat()]
    market, newest = {}, None
    for newest in sorted(released or groups, key=lambda g: g.get("publishedOn") or "", reverse=True)[:5]:
        gid = newest["groupId"]
        products = rec.fetch(f"{pricing.TCGCSV}/{gid}/products").get("results", [])
        prices = rec.fetch(f"{pricing.TCGCSV}/{gid}/prices").get("results", [])
        market = pricing.parse_riftbound_market([(products, prices)])
        if market:
            break
    if not market:
        raise AssertionError("None of the 5 newest released sets had card prices that could be read")
    name, price = next(iter(market.values()))
    result = pricing.lookup_price(Card(name=name, game="Riftbound", set_name=newest.get("name", "")),
                                  fetch=rec.fetch)
    return (f"{len(groups)} sets; newest '{newest.get('name')}' has {len(market)} priced cards. "
            f"Looked up '{name}': ${result.price:,.2f} ({result.matched})")


def check_riftboundgg(rec: Recorder) -> str:
    cards = riftboundgg.load_cards(rec.fetch)
    events = riftboundgg._get("gettournaments?game=riftbound&page=1", rec.fetch)
    if not isinstance(events, list) or not events:
        raise AssertionError("No tournaments listed")
    decks = riftboundgg._get(riftboundgg._deck_query(1, False), rec.fetch)
    if not isinstance(decks, list) or not decks:
        raise AssertionError("No community decks listed")
    converted = [d for d, _ in (riftboundgg.to_deck(raw, cards) for raw in decks) if d]
    unknown = sum(u for _, u in (riftboundgg.to_deck(raw, cards) for raw in decks))
    if not converted:
        raise AssertionError(f"Read {len(decks)} decks but none converted (card codes may have changed)")
    deck = converted[0]
    return (f"{len(cards)} cards; newest event '{events[0].get('name')}' "
            f"({events[0].get('players_count')} players); {len(converted)}/{len(decks)} newest community decks read, "
            f"{unknown} unknown card codes; e.g. legend '{deck.legend}', {sum(c.quantity for c in deck.cards)} cards")


def _topdeck_key() -> str:
    if os.environ.get("TOPDECK_API_KEY"):
        return os.environ["TOPDECK_API_KEY"]
    if Path(DEFAULT_DB_PATH).exists():
        db = CardDatabase(DEFAULT_DB_PATH)
        try:
            return db.get_setting("topdeck_key")
        finally:
            db.close()
    return ""


def check_topdeck(rec: Recorder) -> str:
    key = _topdeck_key()
    if not key:
        raise Skipped(f"No API key yet. Add a free key in Import decklists ({topdeck.KEY_PAGE}).")

    def post(body, api_key):
        data = topdeck._post(body, api_key)
        rec.samples.append((topdeck.API + " " + json.dumps(body), json.dumps(data, ensure_ascii=False)[:SAMPLE_CHARS]))
        return data
    result = topdeck.fetch(key, 14, 2, set(), post=post)
    deck = result.decks[0] if result.decks else None
    return (f"{result.tournaments} Riftbound tournaments in the last 14 days, {len(result.decks)} public decklists"
            + (f"; e.g. {deck.player} placed {deck.placement} at '{deck.event}' with '{deck.legend}', record "
               f"{deck.wins}-{deck.losses}" if deck else ""))


def check_other(rec: Recorder, game: str, name: str) -> str:
    result = pricing.lookup_price(Card(name=name, game=game), fetch=rec.fetch)
    return f"'{name}': ${result.price:,.2f} ({result.matched})"


def check_exchange_rates(rec: Recorder) -> str:
    rates, day, source = currency.fetch_rates(fetch=rec.fetch)
    return f"$1 = £{rates['GBP']:.4f} = €{rates['EUR']:.4f} on {day} ({source})"


CHECKS = [
    ("Riftbound prices (TCGCSV)", check_riftbound_prices),
    ("Riftbound decklists (riftbound.gg)", check_riftboundgg),
    ("Riftbound tournaments (TopDeck.gg)", check_topdeck),
    ("Magic prices (Scryfall)", lambda rec: check_other(rec, "Magic", "Lightning Bolt")),
    ("Pokémon prices (Pokémon TCG API)", lambda rec: check_other(rec, "Pokémon", "Pikachu")),
    ("Yu-Gi-Oh! prices (YGOPRODeck)", lambda rec: check_other(rec, "Yu-Gi-Oh!", "Dark Magician")),
    ("Exchange rates (ECB via frankfurter.dev)", check_exchange_rates),
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
        except Skipped as e:
            status, detail = "SKIP", str(e)
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
