"""Meta tracker: store tournament decklists and see which cards are played most.

Decklists are pasted or imported as text, in the formats that Riftbound
deck builders and meta sites export, e.g.

    Legend: Kennen - Heart of the Tempest
    Champion: 1 Kennen, Storm of Shuriken
    MainDeck:
    3 Traveling Merchant
    2x Tideturner
    Runes:
    Chaos Rune (x6)
    Order Rune x6
    Battlefields:
    Forbidding Waste (210)
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .db import CardDatabase, name_key

LEGEND, CHAMPION, MAIN, RUNES, BATTLEFIELDS, SIDEBOARD = (
    "Legend", "Champion", "Main deck", "Runes", "Battlefields", "Sideboard",
)
SECTIONS = [LEGEND, CHAMPION, MAIN, RUNES, BATTLEFIELDS, SIDEBOARD]

_SECTION_NAMES = {
    "legend": LEGEND,
    "legends": LEGEND,
    "champion": CHAMPION,
    "chosenchampion": CHAMPION,
    "champions": CHAMPION,
    "championunit": CHAMPION,
    "main": MAIN,
    "maindeck": MAIN,
    "deck": MAIN,
    "mainboard": MAIN,
    "rune": RUNES,
    "runes": RUNES,
    "runedeck": RUNES,
    "battlefield": BATTLEFIELDS,
    "battlefields": BATTLEFIELDS,
    "sideboard": SIDEBOARD,
    "side": SIDEBOARD,
    "sidedeck": SIDEBOARD,
}


def typical_copies(avg: float) -> int:
    """Copies to aim for when decks play `avg` on average (2.5 -> 3)."""
    return max(1, int(avg + 0.5))


card_key = name_key


@dataclass
class DeckCard:
    section: str
    name: str
    quantity: int


@dataclass
class Deck:
    name: str = ""
    legend: str = ""
    player: str = ""
    event: str = ""
    placement: int | None = None
    date: str = ""
    notes: str = ""
    source_id: str = ""  # where an imported deck came from, e.g. "riftboundgg:t:<slug>"
    players: int | None = None  # size of the event
    wins: int | None = None     # match record, when known
    losses: int | None = None
    ties: int | None = None
    cards: list[DeckCard] = field(default_factory=list)
    id: int | None = None

    @property
    def main_count(self) -> int:
        return sum(c.quantity for c in self.cards if c.section in (CHAMPION, MAIN))


# --- parsing ---------------------------------------------------------------

_QTY_FIRST = re.compile(r"^(\d+)\s*[x×]?\s+(.+)$", re.I)
_QTY_LAST = re.compile(r"^(.+?)\s*[(\[]?\s*[x×]\s*(\d+)\s*[)\]]?$", re.I)
_TRAILING_CODE = re.compile(r"\s*[(\[]\s*(?:[A-Z]{2,5}[\s-]*)?\d+[a-z]?(?:/\d+)?\s*[)\]]\s*$", re.I)


def _section_of(line: str) -> tuple[str | None, str]:
    """If `line` starts a section ("Runes:", "## Main Deck (40)", "Legend: X"),
    return (section, rest of the line)."""
    head, sep, rest = line.partition(":")
    label = re.sub(r"\(\d+\)", "", head).strip("#*=- ")
    if card_key(label) not in _SECTION_NAMES:
        return None, line
    # Without a colon, only a line that is just the heading counts.
    if sep or re.fullmatch(r"[#*=\s-]*[A-Za-z ]+\s*(\(\d+\))?[\s#*=-]*", line):
        return _SECTION_NAMES[card_key(label)], rest.strip()
    return None, line


def _parse_card(text: str) -> tuple[str, int] | None:
    text = text.strip().strip("-•*").strip()
    if not text:
        return None
    qty = 1
    m = _QTY_FIRST.match(text)
    if m:
        qty, text = int(m.group(1)), m.group(2)
    else:
        m = _QTY_LAST.match(text)
        if m:
            text, qty = m.group(1), int(m.group(2))
    text = _TRAILING_CODE.sub("", text).strip()
    return (text, qty) if text and qty > 0 else None


def parse_decklist(text: str) -> Deck:
    """Read a text decklist. Lines before any section heading count as the
    main deck; the legend is taken from the Legend section."""
    deck = Deck()
    section = MAIN
    totals: dict[tuple[str, str], DeckCard] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        new_section, rest = _section_of(line)
        if new_section:
            section = new_section
            line = rest
            if not line:
                continue
        # "Runes: 9 Chaos Rune, 3 Order Rune" - split only before a quantity,
        # because card names themselves contain commas.
        for part in re.split(r",\s*(?=\d+\s*[x×]?\s)", line):
            parsed = _parse_card(part)
            if not parsed:
                continue
            name, qty = parsed
            key = (section, card_key(name))
            if key in totals:
                totals[key].quantity += qty
            else:
                totals[key] = DeckCard(section, name, qty)
                deck.cards.append(totals[key])
            # A deck has one legend and one chosen champion, so whatever
            # follows them without a heading belongs to the main deck.
            if section in (LEGEND, CHAMPION):
                section = MAIN
    legends = [c.name for c in deck.cards if c.section == LEGEND]
    if legends:
        deck.legend = legends[0]
    return deck


# --- storage and statistics -------------------------------------------------


@dataclass
class CardUsage:
    name: str
    section: str
    decks: int  # number of decks playing it
    share: float  # fraction of decks playing it (0-1)
    avg_copies: float  # average copies in the decks that play it
    total_copies: int
    owned: int = 0


@dataclass
class LegendShare:
    legend: str
    decks: int
    share: float
    best_placement: int | None


class MetaTracker:
    def __init__(self, db: CardDatabase):
        self.db = db
        self.conn = db.conn
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL DEFAULT '',
                legend TEXT NOT NULL DEFAULT '',
                player TEXT NOT NULL DEFAULT '',
                event TEXT NOT NULL DEFAULT '',
                placement INTEGER,
                date TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS deck_cards (
                deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                section TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS deck_cards_deck ON deck_cards(deck_id);
            """
        )
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(decks)")}
        if "source_id" not in columns:
            self.conn.execute("ALTER TABLE decks ADD COLUMN source_id TEXT NOT NULL DEFAULT ''")
        for column in ("players", "wins", "losses", "ties"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE decks ADD COLUMN {column} INTEGER")
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS decks_source ON decks(source_id) WHERE source_id != ''"
        )
        self.conn.commit()

    def known_sources(self, prefix: str) -> set[str]:
        """source_ids of imported decks starting with `prefix`."""
        rows = self.conn.execute("SELECT source_id FROM decks WHERE source_id LIKE ?", (prefix + "%",))
        return {r["source_id"] for r in rows}

    def add_deck(self, deck: Deck) -> int:
        if not deck.cards:
            raise ValueError("The decklist has no cards in it")
        if not deck.legend:
            legends = [c.name for c in deck.cards if c.section == LEGEND]
            deck.legend = legends[0] if legends else ""
        if not deck.date:
            deck.date = date.today().isoformat()
        cur = self.conn.execute(
            "INSERT INTO decks (name, legend, player, event, placement, date, notes, source_id, "
            "players, wins, losses, ties) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (deck.name, deck.legend, deck.player, deck.event, deck.placement, deck.date, deck.notes, deck.source_id,
             deck.players, deck.wins, deck.losses, deck.ties),
        )
        deck.id = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO deck_cards (deck_id, section, name, quantity) VALUES (?, ?, ?, ?)",
            [(deck.id, c.section, c.name, c.quantity) for c in deck.cards],
        )
        self.conn.commit()
        return deck.id

    def delete_deck(self, deck_id: int) -> None:
        self.conn.execute("DELETE FROM deck_cards WHERE deck_id = ?", (deck_id,))
        self.conn.execute("DELETE FROM decks WHERE id = ?", (deck_id,))
        self.conn.commit()

    def get_deck(self, deck_id: int) -> Deck | None:
        row = self.conn.execute("SELECT * FROM decks WHERE id = ?", (deck_id,)).fetchone()
        if not row:
            return None
        deck = Deck(**{k: row[k] for k in row.keys()})
        deck.cards = [
            DeckCard(r["section"], r["name"], r["quantity"])
            for r in self.conn.execute(
                "SELECT section, name, quantity FROM deck_cards WHERE deck_id = ? ORDER BY rowid", (deck_id,)
            )
        ]
        return deck

    def decks(self, legend: str = "", since: str = "", top: int | None = None) -> list[Deck]:
        """Decks matching the filters (without their card lists), newest first.

        `legend` matches regardless of punctuation, `since` is an ISO date,
        and `top` keeps only decks that placed that well or better.
        """
        query = "SELECT * FROM decks WHERE 1=1"
        params: list = []
        if since:
            query += " AND date >= ?"
            params.append(since)
        if top:
            query += " AND placement IS NOT NULL AND placement <= ?"
            params.append(top)
        query += " ORDER BY date DESC, id DESC"
        result = [Deck(**{k: r[k] for k in r.keys()}) for r in self.conn.execute(query, params)]
        if legend:
            result = [d for d in result if card_key(d.legend) == card_key(legend)]
        return result

    def legends(self) -> list[str]:
        seen: dict[str, str] = {}
        for (name,) in self.conn.execute("SELECT legend FROM decks WHERE legend != '' ORDER BY legend COLLATE NOCASE"):
            seen.setdefault(card_key(name), name)
        return list(seen.values())

    def card_usage(self, legend: str = "", since: str = "", top: int | None = None,
                   include_runes: bool = False) -> list[CardUsage]:
        """How often each card appears across the matching decks, most played first."""
        decks = self.decks(legend, since, top)
        if not decks:
            return []
        ids = [d.id for d in decks]
        skip = {LEGEND} | (set() if include_runes else {RUNES})
        per_card: dict[str, dict] = {}
        for chunk_start in range(0, len(ids), 500):
            chunk = ids[chunk_start:chunk_start + 500]
            rows = self.conn.execute(
                f"SELECT deck_id, section, name, quantity FROM deck_cards "
                f"WHERE deck_id IN ({','.join('?' * len(chunk))})",
                chunk,
            )
            for r in rows:
                if r["section"] in skip:
                    continue
                key = card_key(r["name"])
                entry = per_card.setdefault(key, {"names": {}, "sections": {}, "decks": set(), "copies": 0})
                entry["names"][r["name"]] = entry["names"].get(r["name"], 0) + 1
                entry["sections"][r["section"]] = entry["sections"].get(r["section"], 0) + 1
                entry["decks"].add(r["deck_id"])
                entry["copies"] += r["quantity"]

        owned = self.owned_counts()
        usage = []
        for key, e in per_card.items():
            n = len(e["decks"])
            usage.append(CardUsage(
                name=max(e["names"], key=e["names"].get),
                section=max(e["sections"], key=e["sections"].get),
                decks=n,
                share=n / len(decks),
                avg_copies=e["copies"] / n,
                total_copies=e["copies"],
                owned=owned.get(key, 0),
            ))
        usage.sort(key=lambda u: (-u.decks, -u.total_copies, u.name.lower()))
        return usage

    def legend_shares(self, since: str = "", top: int | None = None) -> list[LegendShare]:
        decks = self.decks("", since, top)
        groups: dict[str, list[Deck]] = {}
        for d in decks:
            groups.setdefault(card_key(d.legend or "(unknown)"), []).append(d)
        shares = []
        for members in groups.values():
            placements = [d.placement for d in members if d.placement]
            shares.append(LegendShare(
                legend=members[0].legend or "(unknown)",
                decks=len(members),
                share=len(members) / len(decks),
                best_placement=min(placements) if placements else None,
            ))
        shares.sort(key=lambda s: (-s.decks, s.legend.lower()))
        return shares

    def owned_counts(self) -> dict[str, int]:
        """Copies of each card in the collection (Riftbound or no game set)."""
        counts: dict[str, int] = {}
        for card in self.db.search():
            game = card_key(card.game)
            if game and "riftbound" not in game:
                continue
            key = card_key(card.name)
            counts[key] = counts.get(key, 0) + card.quantity
        return counts
