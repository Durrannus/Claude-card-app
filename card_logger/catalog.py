"""Every Riftbound printing with its current price, from riftbound.gg's card list.

riftbound.gg's card list (api.dotgg.gg/cgfw/getcards) covers every printing
(regular, showcase, promo…) with its TCGplayer market price for normal and
foil copies, the price change over the last day and the last 7 days, and
the Cardmarket (EUR) price. The 7-day change means price trends show from
the very first download; saving each day's prices builds a longer history
for the charts.
"""

import re
from dataclasses import dataclass
from datetime import date

from . import riftboundgg
from .db import CardDatabase


@dataclass
class CatalogCard:
    code: str             # e.g. "OGN-030a"
    name: str
    set_name: str = ""
    rarity: str = ""
    card_type: str = ""
    price: float = 0.0        # TCGplayer market price, normal copy (0 = none)
    foil_price: float = 0.0   # TCGplayer market price, foil copy
    change_1d: float = 0.0    # absolute change of the main price over a day
    change_7d: float = 0.0    # ... and over 7 days
    cm_price: float = 0.0     # Cardmarket, EUR
    image: str = ""
    version: str = ""         # Standard, Alternate art, Overnumbered, Signature… (see classify)
    detail: str = ""          # e.g. which event a promo is from

    @property
    def base_name(self) -> str:
        """The card's name without a trailing "(… Promo)" note."""
        return re.sub(r"\s*\([^)]*\)\s*$", "", self.name)

    def number_key(self) -> tuple:
        """Sort key giving set, then number, then letter: OGN-030 < OGN-030a < OGN-031."""
        code = self.code.upper().replace(" ", "")
        m = re.match(r"^([A-Z0-9]+)-([A-Z]*)(\d+)(.*)$", code)
        if not m:
            return (code, "", 0, "")
        return (m.group(1), m.group(2), int(m.group(3)), m.group(4))

    @property
    def main_price(self) -> float:
        """Normal price if the card comes in normal, otherwise foil."""
        return self.price or self.foil_price

    @property
    def is_foil_only(self) -> bool:
        return not self.price and bool(self.foil_price)

    def change_pct(self, days: int) -> float | None:
        delta = self.change_7d if days == 7 else self.change_1d
        before = self.main_price - delta
        return delta / before if before > 0 and self.main_price else None


VERSIONS = ["Standard", "Alternate art", "Overnumbered", "Signature", "Special (SP)", "Showcase", "Promo",
            "Token", "Oversized"]


def classify(code: str, name: str, rarity: str, set_max: dict[str, int]) -> tuple[str, str]:
    """(version, detail) for a printing, from its code and name.

    Codes seen on riftbound.gg: OGN-030 (standard, or overnumbered when a
    showcase card is numbered past the set's last regular card), OGN-030a /
    VEN-021A (alternate art), OGN-303-STAR (signature), VEN-SP3 (special),
    …-P and Nexus Night "b" versions (promos), UNL-T02 (tokens), and
    OGN-279/298 (oversized).
    """
    c = code.upper().replace(" ", "")
    paren = re.search(r"\(([^)]*)\)\s*$", name)
    detail = paren.group(1) if paren else ""
    upper_name = name.upper()
    prefix, _, rest = c.partition("-")
    if "OVERSIZED" in upper_name:
        return "Oversized", ""
    if re.match(r"^T\d", rest):
        return "Token", detail
    if "-STAR" in c or "*" in c:
        return "Signature", detail
    if re.match(r"^SP\d", rest):
        return "Special (SP)", detail
    if re.search(r"-P(\d+|-[A-Z]+)?$", c) or rest == "P" or "PROMO" in upper_name or "NEXUS NIGHT" in upper_name:
        return "Promo", detail
    if re.match(r"^R?\d+[A-Z]$", rest) or rest.endswith("-A"):
        return "Alternate art", detail
    number = re.match(r"^R?(\d+)", rest)
    if rarity == "Showcase":
        if number and set_max.get(prefix) and int(number.group(1)) > set_max[prefix]:
            return "Overnumbered", detail
        return "Showcase", detail
    return "Standard", detail


def classify_all(cards: list[CatalogCard]) -> list[CatalogCard]:
    """Fill in each card's version; overnumbering is judged against the
    highest regular (non-showcase) number in its set."""
    set_max: dict[str, int] = {}
    for c in cards:
        m = re.fullmatch(r"([A-Z0-9]+)-(\d+)", c.code.upper())
        if m and c.rarity != "Showcase":
            set_max[m.group(1)] = max(set_max.get(m.group(1), 0), int(m.group(2)))
    for c in cards:
        c.version, c.detail = classify(c.code, c.name, c.rarity, set_max)
    return cards


def _f(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def parse(raw: list) -> list[CatalogCard]:
    cards = []
    for c in raw:
        if not isinstance(c, dict) or not c.get("id") or not c.get("name"):
            continue
        price, foil = _f(c.get("price")), _f(c.get("foilPrice"))
        foil_only = not price and foil
        cards.append(CatalogCard(
            # Codes keep their printed case (OGN-030a); names lose stray line breaks.
            code=str(c["id"]).strip(), name=" ".join(str(c["name"]).split()), set_name=str(c.get("set_name") or ""),
            rarity=str(c.get("rarity") or ""), card_type=", ".join(c.get("type") or []),
            price=price, foil_price=foil,
            change_1d=_f(c.get("deltaFoilPrice") if foil_only else c.get("deltaPrice")),
            change_7d=_f(c.get("delta7dPriceFoil") if foil_only else c.get("delta7dPrice")),
            cm_price=_f(c.get("cmPrice")) or _f(c.get("cmFoilPrice")), image=str(c.get("image") or ""),
        ))
    return classify_all(cards)


def fetch(fetch=None) -> list[CatalogCard]:
    raw = riftboundgg._get("getcards?game=riftbound", fetch)
    cards = parse(raw if isinstance(raw, list) else [])
    if not cards:
        raise riftboundgg.FetchError("riftbound.gg's card list came back empty; its format may have changed.")
    return cards


def _ensure_tables(db: CardDatabase) -> None:
    db.conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS catalog (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, set_name TEXT, rarity TEXT, card_type TEXT,
            price REAL, foil_price REAL, change_1d REAL, change_7d REAL, cm_price REAL, image TEXT
        );
        CREATE TABLE IF NOT EXISTS catalog_history (
            code TEXT NOT NULL, day TEXT NOT NULL, price REAL, foil_price REAL, PRIMARY KEY (code, day)
        );
        """
    )


def save(db: CardDatabase, cards: list[CatalogCard], day: str | None = None) -> None:
    """Replace the catalogue and record today's prices in its history."""
    _ensure_tables(db)
    day = day or date.today().isoformat()
    db.conn.execute("DELETE FROM catalog")
    db.conn.executemany(
        "INSERT OR REPLACE INTO catalog VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(c.code, c.name, c.set_name, c.rarity, c.card_type, c.price, c.foil_price, c.change_1d, c.change_7d,
          c.cm_price, c.image) for c in cards],
    )
    db.conn.executemany(
        "INSERT OR REPLACE INTO catalog_history VALUES (?, ?, ?, ?)",
        [(c.code, day, c.price, c.foil_price) for c in cards if c.main_price],
    )
    db.set_setting("catalog_updated", day)  # commits


def load(db: CardDatabase) -> list[CatalogCard]:
    _ensure_tables(db)
    rows = db.conn.execute("SELECT * FROM catalog ORDER BY name COLLATE NOCASE, code")
    return classify_all([CatalogCard(**dict(r)) for r in rows])


def history(db: CardDatabase, card: CatalogCard) -> list[tuple[str, float]]:
    """(day, price) for one printing, using the same normal/foil price as main_price."""
    _ensure_tables(db)
    rows = db.conn.execute("SELECT day, price, foil_price FROM catalog_history WHERE code = ? ORDER BY day",
                           (card.code,))
    points = []
    for r in rows:
        value = r["foil_price"] if card.is_foil_only else r["price"]
        if value:
            points.append((r["day"], value))
    return points
