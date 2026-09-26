"""SQLite storage for the card collection."""

import csv
import os
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import date
from pathlib import Path

DEFAULT_DB_PATH = Path(
    os.environ.get("CARD_LOGGER_DB", Path.home() / ".card_logger" / "cards.db")
)

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
    id: int | None = None


CARD_FIELDS = [f.name for f in fields(Card) if f.name != "id"]


def _row_to_card(row: sqlite3.Row) -> Card:
    return Card(**{key: row[key] for key in row.keys()})


class CardDatabase:
    def __init__(self, path: str | Path = DEFAULT_DB_PATH):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
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
                date_added TEXT NOT NULL DEFAULT ''
            )
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
        self.conn.commit()
        card.id = cur.lastrowid
        return card.id

    def update(self, card: Card) -> None:
        if card.id is None:
            raise ValueError("Cannot update a card without an id")
        _validate(card)
        assignments = ", ".join(f"{f} = ?" for f in CARD_FIELDS)
        values = [getattr(card, f) for f in CARD_FIELDS] + [card.id]
        self.conn.execute(f"UPDATE cards SET {assignments} WHERE id = ?", values)
        self.conn.commit()

    def delete(self, card_id: int) -> None:
        self.conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self.conn.commit()

    def get(self, card_id: int) -> Card | None:
        row = self.conn.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return _row_to_card(row) if row else None

    def search(self, text: str = "", game: str = "") -> list[Card]:
        """Return cards whose name, set, number, rarity or notes contain `text`."""
        query = "SELECT * FROM cards WHERE 1=1"
        params: list = []
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

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS entries, COALESCE(SUM(quantity), 0) AS total_cards, "
            "COALESCE(SUM(quantity * value), 0) AS total_value FROM cards"
        ).fetchone()
        return {
            "entries": row["entries"],
            "total_cards": row["total_cards"],
            "total_value": round(row["total_value"], 2),
        }

    def export_csv(self, path: str | Path) -> int:
        cards = self.search()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CARD_FIELDS)
            writer.writeheader()
            for card in cards:
                row = asdict(card)
                row.pop("id")
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
