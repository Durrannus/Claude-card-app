"""Every Riftbound printing with its current price, from riftbound.gg's card list.

riftbound.gg's card list (api.dotgg.gg/cgfw/getcards) covers every printing
(regular, showcase, promo…) with its TCGplayer market price for normal and
foil copies, the price change over the last day and the last 7 days, and
the Cardmarket (EUR) price. The 7-day change means price trends show from
the very first download; saving each day's prices builds a longer history
for the charts.
"""

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
            code=str(c["id"]).upper(), name=str(c["name"]), set_name=str(c.get("set_name") or ""),
            rarity=str(c.get("rarity") or ""), card_type=", ".join(c.get("type") or []),
            price=price, foil_price=foil,
            change_1d=_f(c.get("deltaFoilPrice") if foil_only else c.get("deltaPrice")),
            change_7d=_f(c.get("delta7dPriceFoil") if foil_only else c.get("delta7dPrice")),
            cm_price=_f(c.get("cmPrice")) or _f(c.get("cmFoilPrice")), image=str(c.get("image") or ""),
        ))
    return cards


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
    return [CatalogCard(**dict(r)) for r in rows]


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
