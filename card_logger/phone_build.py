"""Build the iPhone (web) app's site: fresh market data plus the app files.

Run daily by the GitHub Actions workflow in .github/workflows/phone.yml:

    python -m card_logger.phone_build --db shared.db --site _site

It updates `shared.db` (kept between runs, so price history and decklists
build up) the same way the desktop app's "Get latest data" does: every
Riftbound card's price, TCGplayer market prices for meta cards, exchange
rates, and new decklists from riftbound.gg and (when the TOPDECK_API_KEY
environment variable is set) TopDeck.gg. Then it writes the site:

    _site/            the phone app (copied from phone/)
    _site/py/         this package, which the phone runs with Pyodide
    _site/data/market.db  the market data the phone merges into its own database
    _site/data/info.json  when it was built and what it holds

A failing source is reported and skipped, so the rest still publishes.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import catalog, currency, pricing, riftboundgg, topdeck
from .db import CardDatabase
from .meta import MetaTracker

ROOT = Path(__file__).resolve().parent.parent
FIRST_IMPORT_DAYS = 45  # an empty database starts with this much history
DAILY_IMPORT_DAYS = 30  # later runs look this far back for decklists they don't have yet
MIN_PLAYERS = 8         # TopDeck.gg events smaller than this are skipped, as in the desktop app
# The market tables the phone copies into its own database (see phone_api.merge_market).
MARKET_TABLES = ["catalog", "catalog_history", "decks", "deck_cards", "price_history", "settings"]


def log(message: str) -> None:
    print(message, flush=True)


def update(db: CardDatabase, topdeck_key: str = "") -> list[str]:
    """Refresh everything; returns the problems met (empty when all went well)."""
    problems = []
    meta = MetaTracker(db)

    def step(label, work):
        log(f"• {label}…")
        try:
            result = work()
            log(f"  {result}")
        except Exception as e:  # keep going: publish what we have
            problems.append(f"{label}: {e}")
            log(f"  FAILED: {e}")
            traceback.print_exc()

    def prices():
        cards = catalog.fetch()
        catalog.save(db, cards)
        return f"{len(cards):,} printings"

    def snapshot():
        found = pricing.riftbound_market_prices()
        for name, price in found.values():
            db.record_price(name, price, game="Riftbound", commit=False)
        db.conn.commit()
        return f"{len(found):,} TCGplayer market prices saved"

    def rates():
        return currency.update_rates(db)

    days = DAILY_IMPORT_DAYS if meta.decks() else FIRST_IMPORT_DAYS

    def from_riftboundgg():
        result = riftboundgg.fetch(days, meta.known_sources(""), report=lambda m: None)
        riftboundgg.save(meta, result)
        return f"{result.tournament_decks} tournament and {result.community_decks} community decks added"

    def from_topdeck():
        result = topdeck.fetch(topdeck_key, days, MIN_PLAYERS, meta.known_sources(""), report=lambda m: None)
        added = topdeck.save(meta, result)
        return f"{added} decks from {result.tournaments} tournaments added"

    step("Card prices (riftbound.gg)", prices)
    step("TCGplayer market prices (TCGCSV)", snapshot)
    step("Exchange rates", rates)
    step("Decklists (riftbound.gg)", from_riftboundgg)
    if topdeck_key:
        step("Tournaments (TopDeck.gg)", from_topdeck)
    else:
        log("• Tournaments (TopDeck.gg): skipped, no TOPDECK_API_KEY secret")
    return problems


def write_site(db: CardDatabase, site: Path, problems: list[str]) -> None:
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(ROOT / "phone", site)

    py = site / "py" / "card_logger"
    py.mkdir(parents=True)
    files = []
    for f in sorted((ROOT / "card_logger").glob("*.py")):
        shutil.copy2(f, py / f.name)
        files.append(f"card_logger/{f.name}")
    shutil.copy2(ROOT / "card_logger" / "VERSION", py / "VERSION")
    files.append("card_logger/VERSION")
    (site / "py" / "files.json").write_text(json.dumps(files))

    data = site / "data"
    data.mkdir()
    out = data / "market.db"
    db.conn.commit()
    # Only the shared market tables; nothing personal is ever in shared.db anyway.
    db.conn.execute("VACUUM INTO ?", (str(out),))
    small = sqlite3.connect(out)
    keep = {r[0] for r in small.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for table in keep - set(MARKET_TABLES) - {"sqlite_sequence"}:
        small.execute(f"DROP TABLE {table}")
    small.execute("DELETE FROM settings WHERE key NOT IN ('fx_rates', 'catalog_updated')")
    small.execute("DELETE FROM price_history WHERE card_id IS NOT NULL")
    small.commit()
    small.execute("VACUUM")
    small.close()

    meta = MetaTracker(db)
    info = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "version": (ROOT / "card_logger" / "VERSION").read_text().strip(),
        "printings": db.conn.execute("SELECT COUNT(*) FROM catalog").fetchone()[0],
        "decks": len(meta.decks()),
        "topdeck": bool(meta.known_sources(f"{topdeck.SOURCE}:")),
        "problems": problems,
    }
    (data / "info.json").write_text(json.dumps(info, indent=2))
    log(f"Site written to {site} ({out.stat().st_size / 1e6:.1f} MB of market data)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="shared.db", help="market database kept between runs")
    parser.add_argument("--site", default="_site", help="folder to write the site into")
    parser.add_argument("--no-update", action="store_true", help="only write the site (for testing)")
    args = parser.parse_args(argv)

    db = CardDatabase(args.db, image_dir=Path(args.db).parent / "unused-images")
    MetaTracker(db)  # creates the deck tables
    catalog.load(db)  # creates the catalog tables
    problems = [] if args.no_update else update(db, os.environ.get("TOPDECK_API_KEY", "").strip())
    write_site(db, Path(args.site), problems)
    db.close()
    if problems:
        log("Problems: " + "; ".join(problems))
    return 0


if __name__ == "__main__":
    sys.exit(main())
