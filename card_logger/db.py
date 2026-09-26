"""SQLite storage for the card collection."""

import csv
import os
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.environ.get("CARD_LOGGER_DB", Path.home() / ".card_logger" / "cards.db")
)

IMAGE_TYPES = (".png", ".gif", ".jpg", ".jpeg", ".webp", ".bmp")

CONDITIONS = [
    "Mint",
    "Near Mint",
    "Excellent",
    "Good",
    "Light Played",
    "Played",
    "Poor",
]


@dataclass
class Card:
    name: str
    game: str = ""
    set_name: str = ""
    number: str = ""
    rarity: str = ""
    condition: str = "Near Mint"
    quantity: int = 1
    value: float = 0.0
    notes: str = ""
    date_added: str = ""
    wishlist: bool = False
    image_path: str = ""
    purchase_price: float = 0.0  # what you paid for each copy
    id: int | None = None


CARD_FIELDS = [f.name for f in fields(Card) if f.name != "id"]


def name_key(name: str) -> str:
    """Key for treating spellings like 'Kennen - Heart of the Tempest' and
    'Kennen, Heart of the Tempest' as the same card."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _row_to_card(row: sqlite3.Row) -> Card:
    card = Card(**{key: row[key] for key in row.keys()})
    card.wishlist = bool(card.wishlist)
    return card


def _parse_bool(text: str) -> bool:
    return text.strip().lower() in ("1", "true", "yes", "y", "x")

# Columns added after the first release; older databases get them on open.
_ADDED_COLUMNS = {
    "wishlist": "INTEGER NOT NULL DEFAULT 0",
    "image_path": "TEXT NOT NULL DEFAULT ''",
    "purchase_price": "REAL NOT NULL DEFAULT 0",
}


class CardDatabase:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH, image_dir: str | Path | None = None):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.image_dir = Path(image_dir) if image_dir else self.path.parent / "images"
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                game TEXT NOT NULL DEFAULT '',
                set_name TEXT NOT NULL DEFAULT '',
                number TEXT NOT NULL DEFAULT '',
                rarity TEXT NOT NULL DEFAULT '',
                condition TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                value REAL NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                date_added TEXT NOT NULL DEFAULT '',
                wishlist INTEGER NOT NULL DEFAULT 0,
                image_path TEXT NOT NULL DEFAULT ''
            )
            """
        )
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(cards)")}
        for column, definition in _ADDED_COLUMNS.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE cards ADD COLUMN {column} {definition}")
        self.conn.executescript(
            """
            -- One price per day, either for a card in the collection (card_id)
            -- or for a card name in general (card_id NULL, e.g. meta cards).
            CREATE TABLE IF NOT EXISTS price_history (
                card_id INTEGER,
                name_key TEXT NOT NULL,
                name TEXT NOT NULL,
                game TEXT NOT NULL DEFAULT '',
                day TEXT NOT NULL,
                price REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS price_history_card ON price_history(card_id, day);
            CREATE INDEX IF NOT EXISTS price_history_name ON price_history(name_key, day);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def add(self, card: Card) -> int:
        _validate(card)
        if not card.date_added:
            card.date_added = date.today().isoformat()
        values = [getattr(card, f) for f in CARD_FIELDS]
        cur = self.conn.execute(
            f"INSERT INTO cards ({', '.join(CARD_FIELDS)}) "
            f"VALUES ({', '.join('?' for _ in CARD_FIELDS)})",
            values,
        )
        card.id = cur.lastrowid
        self._record_card_price(card)
        self.conn.commit()
        return card.id

    def update(self, card: Card) -> None:
        if card.id is None:
            raise ValueError("Cannot update a card without an id")
        _validate(card)
        assignments = ", ".join(f"{f} = ?" for f in CARD_FIELDS)
        values = [getattr(card, f) for f in CARD_FIELDS] + [card.id]
        self.conn.execute(f"UPDATE cards SET {assignments} WHERE id = ?", values)
        self._record_card_price(card)
        self.conn.commit()

    # --- price history -------------------------------------------------------

    def _record_card_price(self, card: Card) -> None:
        if card.value > 0:
            self.record_price(card.name, card.value, game=card.game, card_id=card.id, commit=False)

    def record_price(self, name: str, price: float, game: str = "", card_id: int | None = None,
                     day: str | None = None, commit: bool = True) -> None:
        """Save today's (or `day`'s) price, replacing any earlier one that day."""
        day = day or date.today().isoformat()
        key = name_key(name)
        if card_id is None:
            self.conn.execute("DELETE FROM price_history WHERE card_id IS NULL AND name_key = ? AND day = ?",
                              (key, day))
        else:
            self.conn.execute("DELETE FROM price_history WHERE card_id = ? AND day = ?", (card_id, day))
        self.conn.execute(
            "INSERT INTO price_history (card_id, name_key, name, game, day, price) VALUES (?, ?, ?, ?, ?, ?)",
            (card_id, key, name, game, day, price),
        )
        if commit:
            self.conn.commit()

    def price_history(self, card_id: int | None = None, name: str = "") -> list[tuple[str, float]]:
        """(day, price) pairs, oldest first: a collection card's own history
        when `card_id` is given, otherwise the general history for `name`."""
        if card_id is not None:
            rows = self.conn.execute(
                "SELECT day, price FROM price_history WHERE card_id = ? ORDER BY day", (card_id,)
            )
        else:
            rows = self.conn.execute(
                "SELECT day, price FROM price_history WHERE card_id IS NULL AND name_key = ? ORDER BY day",
                (name_key(name),),
            )
        return [(r["day"], r["price"]) for r in rows]

    def last_price_update(self) -> str:
        row = self.conn.execute("SELECT MAX(day) AS day FROM price_history").fetchone()
        return row["day"] or ""

    # --- settings ------------------------------------------------------------

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def delete(self, card_id: int) -> None:
        card = self.get(card_id)
        self.conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self.conn.execute("DELETE FROM price_history WHERE card_id = ?", (card_id,))
        self.conn.commit()
        if card:
            self._remove_stored_image(card.image_path)

    def set_image(self, card: Card, source: str | Path | None) -> None:
        """Copy the photo at `source` into the image folder and attach it to
        `card` (which must already be saved). Pass None to remove the photo."""
        if card.id is None:
            raise ValueError("Save the card before adding a photo")
        old = card.image_path
        if source:
            source = Path(source)
            if source.suffix.lower() not in IMAGE_TYPES:
                raise ValueError(f"Unsupported image type: {source.suffix or '(none)'}")
            self.image_dir.mkdir(parents=True, exist_ok=True)
            dest = self.image_dir / f"card_{card.id}_{date.today():%Y%m%d}_{source.stem[:40]}{source.suffix.lower()}"
            if source.resolve() != dest.resolve():
                shutil.copy2(source, dest)
            card.image_path = str(dest)
        else:
            card.image_path = ""
        self.update(card)
        if old and old != card.image_path:
            self._remove_stored_image(old)

    def _remove_stored_image(self, image_path: str) -> None:
        """Delete a photo, but only if it's one we copied into the image folder."""
        if not image_path:
            return
        path = Path(image_path)
        try:
            if path.resolve().parent == self.image_dir.resolve() and path.exists():
                path.unlink()
        except OSError:
            pass

    def get(self, card_id: int) -> Card | None:
        row = self.conn.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return _row_to_card(row) if row else None

    def search(self, text: str = "", game: str = "", wishlist: bool | None = False) -> list[Card]:
        """Return cards whose name, set, number, rarity or notes contain `text`.

        By default only owned cards are returned; pass wishlist=True for the
        wishlist, or None for both.
        """
        query = "SELECT * FROM cards WHERE 1=1"
        params: list = []
        if wishlist is not None:
            query += " AND wishlist = ?"
            params.append(int(wishlist))
        if text:
            like = f"%{text}%"
            query += (
                " AND (name LIKE ? OR set_name LIKE ? OR number LIKE ?"
                " OR rarity LIKE ? OR notes LIKE ?)"
            )
            params += [like] * 5
        if game:
            query += " AND game = ?"
            params.append(game)
        query += " ORDER BY game COLLATE NOCASE, set_name COLLATE NOCASE, name COLLATE NOCASE"
        return [_row_to_card(r) for r in self.conn.execute(query, params)]

    def games(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT game FROM cards WHERE game != '' ORDER BY game COLLATE NOCASE"
        )
        return [r["game"] for r in rows]

    def stats(self, wishlist: bool = False) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(quantity), 0) AS total_cards, "
            "COALESCE(SUM(quantity * value), 0) AS total_value FROM cards WHERE wishlist = ?",
            (int(wishlist),),
        ).fetchone()
        return {
            "entries": row["entries"],
            "total_cards": row["total_cards"],
            "total_value": round(row["total_value"], 2),
        }

    def export_csv(self, path: str | Path) -> int:
        cards = self.search(wishlist=None)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CARD_FIELDS)
            writer.writeheader()
            for card in cards:
                row = asdict(card)
                row.pop("id")
                row["wishlist"] = "yes" if card.wishlist else ""
                writer.writerow(row)
        return len(cards)

    def import_csv(self, path: str | Path) -> int:
        """Add every row of a CSV (with a header row) to the collection.

        Only a `name` column is required; unknown columns are ignored.
        """
        count = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for line_no, row in enumerate(reader, start=2):
                data = {k: (v or "").strip() for k, v in row.items() if k in CARD_FIELDS}
                if not data.get("name"):
                    continue
                try:
                    data["quantity"] = int(data.get("quantity") or 1)
                    data["value"] = float(data.get("value") or 0)
                    data["purchase_price"] = float(data.get("purchase_price") or 0)
                    data["wishlist"] = _parse_bool(data.get("wishlist", ""))
                    self.add(Card(**data))
                except ValueError as e:
                    raise ValueError(f"Line {line_no}: {e}") from e
                count += 1
        return count


def _validate(card: Card) -> None:
    card.name = card.name.strip()
    if not card.name:
        raise ValueError("Card name is required")
    if card.quantity < 0:
        raise ValueError("Quantity cannot be negative")
    if card.value < 0:
        raise ValueError("Value cannot be negative")
    if card.purchase_price < 0:
        raise ValueError("Purchase price cannot be negative")
