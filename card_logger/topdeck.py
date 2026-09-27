"""Import Riftbound tournament results and decklists from TopDeck.gg.

TopDeck.gg runs many Riftbound events, from store tournaments to large
qualifiers, and offers a free public API (https://topdeck.gg/docs/tournaments-v2).
It needs a free API key from https://topdeck.gg/developers, and asks every
app using it to show "Data provided by TopDeck.gg" with a link.

One request (POST /v2/tournaments) returns every Riftbound tournament from
the last N days with its standings: each player's placing, win/loss record,
legend and, once the event has ended, their decklist.

Network work (fetch) and saving (save) are separate so the download can run
on a background thread and the saving on the UI thread.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .meta import _SECTION_NAMES, LEGEND, MAIN, Deck, DeckCard, MetaTracker, card_key, parse_decklist

API = "https://topdeck.gg/api/v2/tournaments"
KEY_PAGE = "https://topdeck.gg/developers"
GAME = "Riftbound"
FORMAT = "Constructed"  # the API requires a format along with the game
SOURCE = "topdeck"
ATTRIBUTION = "Tournament data provided by TopDeck.gg"
RETRIES = 2
RETRY_PAUSE = 30  # the API allows about 100 requests a minute; bulk queries less


class TopDeckError(Exception):
    """Import failed; the message is suitable for showing to the user."""


def _post(body: dict, api_key: str):
    request = urllib.request.Request(
        API, data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": api_key.strip(), "Content-Type": "application/json",
                 "Accept": "application/json", "User-Agent": "CardCollectionLogger/1.0"},
    )
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise TopDeckError("TopDeck.gg didn't accept the API key. Check it in Import decklists, or get "
                                   f"a new free one at {KEY_PAGE}.") from e
            if (e.code == 429 or e.code >= 500) and attempt < RETRIES:
                time.sleep(RETRY_PAUSE * (attempt + 1))
                continue
            raise TopDeckError(f"TopDeck.gg returned an error ({e.code}). Try again later.") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TopDeckError("Could not reach TopDeck.gg. Check your internet connection.") from e
        except json.JSONDecodeError as e:
            raise TopDeckError("TopDeck.gg sent an unexpected response; its format may have changed.") from e
    raise TopDeckError("TopDeck.gg is busy. Try again in a minute.")


def _count(value) -> int:
    if isinstance(value, dict):
        value = value.get("count", value.get("quantity", value.get("qty", value.get("amount", 1))))
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def cards_from_deckobj(deck_obj) -> list[DeckCard]:
    """deckObj is {"Section": {"Card name": count or {"count": n, ...}}}; lists
    of {"name": ..., "count": n} are accepted too."""
    cards: list[DeckCard] = []
    if not isinstance(deck_obj, dict):
        return cards
    for label, entries in deck_obj.items():
        section = _SECTION_NAMES.get(card_key(str(label)), MAIN)
        if isinstance(entries, dict):
            items = [(name, qty) for name, qty in entries.items()]
        elif isinstance(entries, list):
            items = [(e.get("name"), e) for e in entries if isinstance(e, dict)]
        else:
            continue
        for name, qty in items:
            n = _count(qty)
            if name and n > 0:
                cards.append(DeckCard(section, str(name).strip(), n))
    return cards


def standing_to_deck(standing: dict, tournament: dict, day: str, players: int, position: int) -> Deck | None:
    """`position` is the player's place in the standings list, which comes in
    finishing order; the API doesn't return a separate placing column."""
    cards = cards_from_deckobj(standing.get("deckObj"))
    text = standing.get("decklist")
    if not cards and isinstance(text, str) and text.strip() and not text.strip().startswith("http"):
        cards = parse_decklist(text.replace("~~", "")).cards  # "~~Main Deck~~" section headings
    if not cards:
        return None
    legend = (standing.get("leader") or "").split(" / ")[0].strip()
    if not legend:
        legends = [c.name for c in cards if c.section == LEGEND]
        legend = legends[0] if legends else ""
    def number(key):
        v = standing.get(key)
        return int(v) if isinstance(v, (int, float)) else None
    return Deck(
        name="", legend=legend, player=str(standing.get("name") or ""),
        event=str(tournament.get("tournamentName") or "TopDeck.gg tournament"),
        placement=number("standing") or position, date=day, players=players or None,
        wins=number("wins"), losses=number("losses"), ties=number("draws"),
        notes=f"{ATTRIBUTION} · {players} players",
        source_id=f"{SOURCE}:{tournament.get('TID')}:{standing.get('id') or standing.get('name')}",
        cards=cards,
    )


@dataclass
class FetchResult:
    decks: list[Deck] = field(default_factory=list)
    tournaments: int = 0
    without_lists: int = 0  # players whose decklist isn't public


def fetch(api_key: str, days: int, min_players: int, known_sources: set[str], post=None,
          today: date | None = None, report=None) -> FetchResult:
    if not api_key or not api_key.strip():
        raise TopDeckError(f"Add your free TopDeck.gg API key in Import decklists first ({KEY_PAGE}).")
    today = today or datetime.now(timezone.utc).date()
    if report:
        report("Downloading tournaments from TopDeck.gg…")
    body = {"game": GAME, "format": FORMAT, "last": days, "participantMin": min_players,
            "columns": ["name", "id", "decklist", "wins", "losses", "draws"]}
    data = (post or _post)(body, api_key)
    if not isinstance(data, list):
        raise TopDeckError("TopDeck.gg sent an unexpected response; its format may have changed.")
    result = FetchResult()
    for t in data:
        if not isinstance(t, dict) or t.get("isTeamEvent"):
            continue
        try:
            day = datetime.fromtimestamp(int(t.get("startDate")), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        if day >= today.isoformat():
            continue  # may still be running; decklists appear once it has ended
        standings = [s for s in (t.get("standings") or []) if isinstance(s, dict)]
        result.tournaments += 1
        for position, s in enumerate(standings, start=1):
            deck = standing_to_deck(s, t, day, len(standings), position)
            if deck is None:
                result.without_lists += 1
            elif deck.source_id not in known_sources:
                known_sources.add(deck.source_id)
                result.decks.append(deck)
    return result


def save(meta: MetaTracker, result: FetchResult) -> int:
    added = 0
    known = meta.known_sources(f"{SOURCE}:")
    for deck in result.decks:
        if deck.source_id not in known:
            meta.add_deck(deck)
            known.add(deck.source_id)
            added += 1
    return added
