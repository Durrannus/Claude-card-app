"""Import Riftbound tournament decklists from Limitless (play.limitlesstcg.com).

Limitless runs online and in-store tournaments and publishes their results
through a public API (https://docs.limitlesstcg.com/developer.html): a list
of tournaments, and for each one the standings with every player's placing
and, when the organiser made them public, their decklist.

Network work (fetch_events) and saving (save_events) are separate so the
download can run on a background thread and the saving on the UI thread.
"""

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import pricing
from .meta import _SECTION_NAMES, LEGEND, MAIN, Deck, DeckCard, MetaTracker, card_key

API = "https://play.limitlesstcg.com/api"
SOURCE = "limitless"
PAUSE = 0.3  # seconds between requests, to be polite to a free service
MAX_PAGES = 20
GAME_ID_GUESSES = ["RB", "RIFTBOUND", "RFB", "LOL", "LOLTCG"]


class ImportError_(Exception):
    """Import failed; the message is suitable for showing to the user."""


@dataclass
class Event:
    tournament_id: str
    name: str
    date: str
    players: int
    decks: list[Deck] = field(default_factory=list)


def _fetch(fetch, path: str, **params):
    url = API + path
    if params:
        url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
    try:
        return fetch(url)
    except pricing.PriceLookupError as e:
        message = str(e).replace("the price service", "Limitless").replace("Price service", "Limitless")
        raise ImportError_(message) from e


def find_game_id(fetch=pricing._get_json) -> str:
    """Limitless's code for Riftbound, from its list of games if available."""
    games = _fetch(fetch, "/games")
    if isinstance(games, list):
        for g in games:
            text = f"{g.get('id', '')} {g.get('name', '')}".lower()
            if "riftbound" in text:
                return str(g.get("id"))
    for guess in GAME_ID_GUESSES:
        found = _fetch(fetch, "/tournaments", game=guess, limit=1)
        if isinstance(found, list) and found:
            return guess
        time.sleep(PAUSE)
    raise ImportError_("Couldn't find Riftbound on Limitless. Their API may have changed.")


def fetch_events(game_id: str, days: int, min_players: int, already_checked: set[str],
                 fetch=pricing._get_json, report=None, today: date | None = None) -> list[Event]:
    """Finished tournaments from the last `days` days with at least
    `min_players`, skipping tournament ids in `already_checked`."""
    today = today or datetime.now(timezone.utc).date()
    cutoff = (today - timedelta(days=days)).isoformat()
    candidates = []
    for page in range(1, MAX_PAGES + 1):
        batch = _fetch(fetch, "/tournaments", game=game_id, limit=50, page=page)
        if not isinstance(batch, list) or not batch:
            break
        for t in batch:
            day = str(t.get("date", ""))[:10]
            tid = str(t.get("id", ""))
            # Today's events may still be running; they're picked up next time.
            if tid and cutoff <= day < today.isoformat() and (t.get("players") or 0) >= min_players \
                    and tid not in already_checked:
                candidates.append(Event(tid, t.get("name") or "Limitless tournament", day, t.get("players") or 0))
        oldest = min(str(t.get("date", ""))[:10] for t in batch)
        if oldest < cutoff:
            break
        time.sleep(PAUSE)

    for i, event in enumerate(candidates, 1):
        if report:
            report(f"Downloading tournament {i}/{len(candidates)}: {event.name}")
        standings = _fetch(fetch, f"/tournaments/{event.tournament_id}/standings")
        if isinstance(standings, list):
            event.decks = [d for d in (standing_to_deck(event, s) for s in standings) if d]
        time.sleep(PAUSE)
    return candidates


def standing_to_deck(event: Event, standing: dict) -> Deck | None:
    """Turn one player's standing into a Deck, or None without a decklist."""
    cards = parse_limitless_decklist(standing.get("decklist"))
    if not cards:
        return None
    player = standing.get("player") or standing.get("name") or ""
    deck = Deck(
        name=((standing.get("deck") or {}).get("name") or ""),
        player=standing.get("name") or player,
        event=event.name,
        placement=standing.get("placing") if isinstance(standing.get("placing"), int) else None,
        date=event.date,
        notes=f"Imported from Limitless · {event.players} players",
        source_id=f"{SOURCE}:{event.tournament_id}:{player}",
        players=event.players or None,
        cards=cards,
    )
    record = standing.get("record") or {}
    if isinstance(record, dict) and isinstance(record.get("wins"), int) and isinstance(record.get("losses"), int):
        deck.wins, deck.losses = record["wins"], record["losses"]
        deck.ties = record.get("ties") if isinstance(record.get("ties"), int) else 0
    legends = [c.name for c in cards if c.section == LEGEND]
    deck.legend = legends[0] if legends else deck.name
    return deck


def _entry(item: dict) -> tuple[str, int] | None:
    name = item.get("name") or item.get("cardName") or item.get("card") or ""
    if isinstance(name, dict):
        name = name.get("name", "")
    count = item.get("count", item.get("quantity", item.get("qty", item.get("amount", 1))))
    try:
        count = int(count)
    except (TypeError, ValueError):
        return None
    return (str(name).strip(), count) if name and count > 0 else None


def _section(label: str) -> str:
    return _SECTION_NAMES.get(card_key(label), MAIN)


def parse_limitless_decklist(decklist) -> list[DeckCard]:
    """Read a decklist in any of the shapes Limitless uses across games:
    {"section": [{"count": 3, "name": ...}, ...], ...}, a flat list of
    entries that name their own section, or {"section": {"Card": 3}}."""
    cards: list[DeckCard] = []
    if isinstance(decklist, dict):
        for label, items in decklist.items():
            section = _section(str(label))
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and (e := _entry(item)):
                        cards.append(DeckCard(section, *e))
            elif isinstance(items, dict):
                for name, count in items.items():
                    if isinstance(count, int) and count > 0:
                        cards.append(DeckCard(section, str(name), count))
    elif isinstance(decklist, list):
        for item in decklist:
            if isinstance(item, dict) and (e := _entry(item)):
                label = item.get("section") or item.get("category") or item.get("type") or ""
                cards.append(DeckCard(_section(str(label)), *e))
    return cards


def save_events(meta: MetaTracker, events: list[Event]) -> tuple[int, int]:
    """Store the decks (skipping any already imported) and remember the
    tournaments as checked. Returns (decks added, tournaments)."""
    meta.conn.execute("CREATE TABLE IF NOT EXISTS imported_events (source_id TEXT PRIMARY KEY, day TEXT)")
    known = meta.known_sources(f"{SOURCE}:")
    added = 0
    for event in events:
        for deck in event.decks:
            if deck.source_id not in known:
                meta.add_deck(deck)
                known.add(deck.source_id)
                added += 1
        meta.conn.execute("INSERT OR REPLACE INTO imported_events VALUES (?, ?)",
                          (f"{SOURCE}:{event.tournament_id}", event.date))
    meta.conn.commit()
    return added, len(events)


def checked_tournaments(meta: MetaTracker) -> set[str]:
    meta.conn.execute("CREATE TABLE IF NOT EXISTS imported_events (source_id TEXT PRIMARY KEY, day TEXT)")
    rows = meta.conn.execute("SELECT source_id FROM imported_events WHERE source_id LIKE ?", (f"{SOURCE}:%",))
    return {r["source_id"].split(":", 1)[1] for r in rows}
