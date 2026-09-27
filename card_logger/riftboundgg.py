"""Import Riftbound decklists from riftbound.gg.

riftbound.gg (part of the DotGG network) serves its data through a public
JSON service at api.dotgg.gg that needs no key. It isn't an officially
documented API, so this module is written to fail with a clear message if
the format changes. Three endpoints are used:

- cgfw/getcards: every card, with its code (e.g. "OGN-126"), name and type,
  used to turn deck codes into names and sort them into legend, runes,
  battlefields and main deck.
- cgfw/getdecks: published decklists, newest first, 30 per page. Tournament
  decks (is_tournament=1) carry the event and finishing place; community
  decks are what players are building right now.
- cgfw/gettournaments: events with their real date and player count, used
  to date tournament decks by when the event happened rather than when the
  list was uploaded.

The service rate-limits bursts, so requests are spaced out and a "slow down"
answer (429) is retried after a pause.

Network work (fetch) and saving (save) are separate so the download can run
on a background thread and the saving on the UI thread.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .meta import BATTLEFIELDS, LEGEND, MAIN, RUNES, Deck, DeckCard, MetaTracker

API = "https://api.dotgg.gg/cgfw"
HEADERS = {"User-Agent": "CardCollectionLogger/1.0", "Origin": "https://riftbound.gg", "Accept": "application/json"}
SOURCE = "riftboundgg"
PAUSE = 1.5            # seconds between requests; the service 429s on bursts
RETRY_PAUSE = 20
RETRIES = 3
PAGE_CAP = 26          # the deck feed returns nothing past about 26 pages
TOURNAMENT_PAGE_CAP = 40
MIN_DECK_SIZE = 50     # smaller lists are unfinished brews, not decks
COMMUNITY_EVENT = "Community decks (riftbound.gg)"

TYPE_SECTIONS = {"legend": LEGEND, "rune": RUNES, "battlefield": BATTLEFIELDS}


class FetchError(Exception):
    """Import failed; the message is suitable for showing to the user."""


def _get(path: str, fetch=None):
    url = f"{API}/{path}"
    return fetch(url) if fetch else http_json(url)


def http_json(url: str):
    """GET a riftbound.gg URL, retrying politely when asked to slow down."""
    for attempt in range(RETRIES + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if (e.code == 429 or e.code >= 500) and attempt < RETRIES:
                time.sleep(RETRY_PAUSE * (attempt + 1))
                continue
            raise FetchError(f"riftbound.gg returned an error ({e.code}). Try again later.") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise FetchError("Could not reach riftbound.gg. Check your internet connection.") from e
        except json.JSONDecodeError as e:
            raise FetchError("riftbound.gg sent an unexpected response; its format may have changed.") from e
    raise FetchError("riftbound.gg is busy. Try again later.")


def _day(unix) -> str:
    try:
        return datetime.fromtimestamp(int(unix), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


# --- cards -------------------------------------------------------------------


def load_cards(fetch=None) -> dict[str, tuple[str, str]]:
    """Card code -> (name, section)."""
    cards = _get("getcards?game=riftbound", fetch)
    if not isinstance(cards, list) or not cards:
        raise FetchError("riftbound.gg's card list came back empty; its format may have changed.")
    result = {}
    for c in cards:
        if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
            continue
        types = [str(t).lower() for t in (c.get("type") or [])]
        section = next((TYPE_SECTIONS[t] for t in types if t in TYPE_SECTIONS), MAIN)
        result[str(c["id"]).upper()] = (str(c["name"]), section)
    return result


def resolve_code(code: str, cards: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Find a card for codes like 'OGN-202-P' (promo), 'UNL-230-STAR' or
    'OGN-301*' (signature) and 'OGN-263-a' (variant), falling back to the
    base printing, since they're all the same card for play purposes."""
    c = str(code).strip().upper()
    candidates = [c]
    c = re.sub(r"-(P|STAR)$", "", c).replace("*", "")
    candidates.append(c)
    c = re.sub(r"-([A-Z])$", r"\1", c)
    candidates.append(c)
    candidates.append(re.sub(r"(\d)[A-Z]$", r"\1", c))
    m = re.match(r"^([A-Z]{2,5})-0*(\d+)([A-Z]?)$", c)
    if m:  # "OGN-7" vs "OGN-007"
        candidates += [f"{m.group(1)}-{int(m.group(2)):03d}{m.group(3)}", f"{m.group(1)}-{int(m.group(2)):03d}"]
    for candidate in candidates:
        if candidate in cards:
            return cards[candidate]
    return None


# --- tournaments -------------------------------------------------------------


@dataclass
class EventInfo:
    slug: str
    name: str
    day: str
    players: int


def load_events(since: str, fetch=None, report=None, pause: float = PAUSE) -> dict[str, EventInfo]:
    """Events from newest back to `since` (ISO date), keyed by slug."""
    events: dict[str, EventInfo] = {}
    for page in range(1, TOURNAMENT_PAGE_CAP + 1):
        if report:
            report(f"Reading riftbound.gg's tournament list… page {page}")
        batch = _get(f"gettournaments?game=riftbound&page={page}", fetch)
        if not isinstance(batch, list) or not batch:
            break
        for t in batch:
            if isinstance(t, dict) and t.get("slug"):
                try:
                    players = int(t.get("players_count") or 0)
                except (TypeError, ValueError):
                    players = 0
                events[t["slug"]] = EventInfo(t["slug"], t.get("name") or t["slug"], _day(t.get("date")), players)
        oldest = min((_day(t.get("date")) for t in batch if isinstance(t, dict)), default="")
        if oldest and oldest < since:
            break
        time.sleep(pause)
    return events


# --- decks ---------------------------------------------------------------------


def _deck_query(page: int, tournament: bool) -> str:
    filters = {"hascrd": [], "nothascrd": [], "youtube": 0, "smartsrch": "", "format": "", "date": "",
               "color": [], "collection": 0, "topset": "", "at": 0, "is_tournament": 1 if tournament else "",
               "legalonly": 1, "priceMin": "", "priceMax": "", "placement": "", "leader": ""}
    rq = {"page": page, "limit": 30, "srt": "date", "direct": "desc", "type": "", "my": 0, "myarchive": 0,
          "fav": 0, "getdecks": filters}
    return "getdecks?game=riftbound&rq=" + urllib.parse.quote(json.dumps(rq, separators=(",", ":")))


def to_deck(raw: dict, cards: dict[str, tuple[str, str]]) -> tuple[Deck | None, int]:
    """Convert one riftbound.gg deck; returns (deck or None, codes not recognised)."""
    entries = raw.get("deck")
    if not isinstance(entries, dict):
        return None, 0
    totals: dict[tuple[str, str], int] = {}
    unknown = 0
    for code, qty in entries.items():
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            continue
        found = resolve_code(code, cards)
        if not found:
            unknown += 1
            continue
        if qty > 0:
            totals[(found[1], found[0])] = totals.get((found[1], found[0]), 0) + qty
    if sum(totals.values()) < MIN_DECK_SIZE:
        return None, unknown
    deck_cards = [DeckCard(section, name, qty) for (section, name), qty in totals.items()]
    legends = [c.name for c in deck_cards if c.section == LEGEND]
    deck = Deck(name=str(raw.get("humanname") or ""), legend=legends[0] if legends else "",
                date=_day(raw.get("date")), cards=deck_cards)
    return deck, unknown


@dataclass
class FetchResult:
    decks: list[Deck] = field(default_factory=list)
    tournament_decks: int = 0
    community_decks: int = 0
    skipped_undated: int = 0
    skipped_small: int = 0
    unknown_codes: int = 0


def fetch(days: int, known_sources: set[str], tournaments: bool = True, community: bool = True,
          fetch=None, report=None, today: date | None = None, pause: float = PAUSE) -> FetchResult:
    """Download decks from the last `days` days that aren't in `known_sources`."""
    today = today or datetime.now(timezone.utc).date()
    since = (today - timedelta(days=days)).isoformat()
    result = FetchResult()
    if report:
        report("Downloading riftbound.gg's card list…")
    cards = load_cards(fetch)
    time.sleep(pause)

    if tournaments:
        events = load_events(since, fetch, report, pause)
        barren = 0  # pages in a row with nothing importable
        for page in range(1, PAGE_CAP + 1):
            if report:
                report(f"Downloading tournament decklists… page {page}")
            batch = _get(_deck_query(page, True), fetch)
            if not isinstance(batch, list) or not batch:
                break
            fresh, usable = 0, 0
            for raw in batch:
                t = raw.get("tournament") or {}
                source_id = f"{SOURCE}:t:{raw.get('slug')}"
                if not raw.get("slug") or source_id in known_sources:
                    continue
                fresh += 1
                event = events.get(t.get("tournament_slug") or "")
                if not event or not event.day or event.day < since:
                    result.skipped_undated += 1  # event older than the look-back, or its date is unknown
                    continue
                deck, unknown = to_deck(raw, cards)
                result.unknown_codes += unknown
                if not deck:
                    result.skipped_small += 1
                    continue
                place = t.get("place")
                deck.placement = int(place) if isinstance(place, (int, str)) and str(place).isdigit() else None
                deck.event, deck.date, deck.players = event.name, event.day, event.players or None
                deck.player = deck.name.split(" - ")[0] if " - " in deck.name else ""
                deck.notes = f"riftbound.gg tournament deck · {event.players} players"
                deck.source_id = source_id
                known_sources.add(source_id)
                result.decks.append(deck)
                result.tournament_decks += 1
                usable += 1
            barren = 0 if usable else barren + 1
            # Stop once a page is all already imported, or two pages in a row
            # are all from events outside the look-back.
            if not fresh or barren >= 2:
                break
            time.sleep(pause)

    if community:
        for page in range(1, PAGE_CAP + 1):
            if report:
                report(f"Downloading community decklists… page {page}")
            batch = _get(_deck_query(page, False), fetch)
            if not isinstance(batch, list) or not batch:
                break
            fresh, too_old = 0, False
            for raw in batch:
                if str(raw.get("is_tournament")) == "1":
                    continue  # handled above, with its event date
                posted = _day(raw.get("date"))
                if posted and posted < since:
                    too_old = True
                    continue
                # Copies of the same list share a fingerprint; count each list once.
                source_id = f"{SOURCE}:c:{raw.get('fingerprint') or raw.get('slug')}"
                if source_id in known_sources:
                    continue
                fresh += 1
                deck, unknown = to_deck(raw, cards)
                result.unknown_codes += unknown
                if not deck:
                    result.skipped_small += 1
                    continue
                deck.event = COMMUNITY_EVENT
                deck.notes = "Published on riftbound.gg"
                deck.source_id = source_id
                known_sources.add(source_id)
                result.decks.append(deck)
                result.community_decks += 1
            if too_old or not fresh:
                break
            time.sleep(pause)
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
