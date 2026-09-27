"""What the iPhone (web) app calls, running in the phone's browser with Pyodide.

The phone app (phone/app.js) calls `call(name, args_json)` and gets JSON
back. Each function here prepares one screen's data using the same modules
as the desktop app, so the numbers, signals and insights match.

The phone keeps one database of its own (your collection, wishlist, sold
prices, settings and any decklists you add), and each day merges in the
shared market data built by phone_build.py: every card's price and history,
exchange rates and tournament decklists.
"""

import csv
import io
import json
from dataclasses import replace
from datetime import date, timedelta

from . import catalog, currency, insight, market, pricing
from .db import CARD_FIELDS, CONDITIONS, Card, CardDatabase, name_key
from .meta import LEGEND, SECTIONS, MetaTracker, card_key, parse_decklist, typical_copies

DB: CardDatabase | None = None
META: MetaTracker | None = None
_cache: dict = {}

PHOTO = "phone-photo"                # image_path of a card whose photo is stored on the phone

EBAY_SITES = ["ebay.co.uk", "ebay.com", "ebay.com.au", "ebay.ca", "ebay.de", "ebay.fr", "ebay.it", "ebay.es"]
WINDOWS = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}
SIGNAL_FILTERS = {
    "All signals": None,
    "Sell": {market.SELL_HYPE, market.SELL_FALLING, market.SELL_SPIKE},
    "Hold": {market.HOLD_RISING, market.HOLD},
    "Buy / watch": {market.BUY_EARLY, market.WATCH},
}
SIGNAL_TAGS = {
    market.SELL_HYPE: "sell", market.SELL_FALLING: "sell", market.SELL_SPIKE: "sell",
    market.BUY_EARLY: "buy", market.WATCH: "watch",
    market.HOLD_RISING: "rising", market.HOLD: "hold", market.NO_SIGNAL: "none",
}
TOP_CHOICES = {"All placements": None, "Winners only": 1, "Top 4": 4, "Top 8": 8, "Top 16": 16, "Top 32": 32}
LOOK_BACK = {"Last 4 weeks": 4, "Last 6 weeks": 6, "Last 8 weeks": 8, "Last 12 weeks": 12}
META_FILTERS = ["Any meta", "Rising in meta", "Falling in meta", "Played in meta", "Not played"]
GAMES = ["Riftbound", "Magic", "Pokémon", "Yu-Gi-Oh!"]
RARITIES = ["Common", "Uncommon", "Rare", "Epic", "Showcase", "Promo"]


def call(name: str, args_json: str = "{}") -> str:
    """Run one API function; errors come back as {"error": message}."""
    try:
        result = API[name](**json.loads(args_json or "{}"))
        return json.dumps({"ok": result})
    except Exception as e:  # shown to the user by the phone app
        import traceback
        traceback.print_exc()
        return json.dumps({"error": str(e) or e.__class__.__name__})


# --- helpers -----------------------------------------------------------------


def _changed(*parts: str) -> None:
    """Forget cached results after data changes ("decks", "cards", "all")."""
    for key in list(_cache):
        if "all" in parts or key[0] in parts:
            del _cache[key]


def _cached(kind: str, key, make):
    k = (kind, key)
    if k not in _cache:
        _cache[k] = make()
    return _cache[k]


def _pct(v: float | None) -> str:
    return "" if v is None else ("0%" if abs(v) < 0.005 else f"{v:+.0%}")


def _friendly_day(iso: str) -> str:
    try:
        days = (date.today() - date.fromisoformat(iso[:10])).days
    except ValueError:
        return iso
    return {0: "Today", 1: "Yesterday"}.get(days, f"{days} days ago")


def _series(points, convert=None) -> list:
    convert = convert or (lambda v: v)
    return [[d, None if v is None else round(convert(v), 4)] for d, v in points]


def _trends(days: int) -> dict:
    return _cached("decks", ("trends", days), lambda: market.meta_trends(META, days))


def _play() -> dict[str, float]:
    return _cached("decks", "play", lambda: {name_key(u.name): u.share for u in META.card_usage(include_runes=True)})


def _catalog() -> list:
    return _cached("market", "catalog", lambda: catalog.load(DB))


def _ebay_site() -> str:
    return DB.get_setting("ebay_site", currency.ebay_site())


# --- start-up ----------------------------------------------------------------


def open_db(path: str, market_path: str = "", built: str = "") -> dict:
    """Open the phone's database, merging in new market data if there is some."""
    global DB, META
    DB = CardDatabase(path, image_dir="/tmp/unused-images")
    META = MetaTracker(DB)
    catalog.load(DB)  # creates the catalog tables on a new phone
    if market_path and built != DB.get_setting("market_built"):
        merge_market(market_path, built)
    currency.configure(DB)
    _changed("all")
    return summary()


def merge_market(path: str, built: str = "") -> None:
    """Replace the shared market data (prices, history, imported decklists,
    exchange rates) with the new download, keeping everything that's yours."""
    c = DB.conn
    c.execute("ATTACH DATABASE ? AS m", (path,))
    try:
        def columns(table):
            return [r["name"] for r in c.execute(f"PRAGMA main.table_info({table})")]
        for table in ("catalog", "catalog_history"):
            cols = ", ".join(columns(table))
            c.execute(f"DELETE FROM main.{table}")
            c.execute(f"INSERT INTO main.{table} ({cols}) SELECT {cols} FROM m.{table}")
        # Imported decks are replaced; decks you added stay. Imported decks get
        # negative ids so they never collide with yours (which count up from 1).
        c.execute("DELETE FROM main.deck_cards WHERE deck_id < 0")
        c.execute("DELETE FROM main.decks WHERE id < 0")
        deck_cols = [col for col in columns("decks") if col != "id"]
        cols = ", ".join(deck_cols)
        c.execute(f"INSERT INTO main.decks (id, {cols}) SELECT -id, {cols} FROM m.decks")
        c.execute("INSERT INTO main.deck_cards (deck_id, section, name, quantity) "
                  "SELECT -deck_id, section, name, quantity FROM m.deck_cards")
        # Market-wide price history (card_id NULL); your cards' own history stays.
        c.execute("DELETE FROM main.price_history WHERE card_id IS NULL")
        c.execute("INSERT INTO main.price_history (card_id, name_key, name, game, day, price) "
                  "SELECT NULL, name_key, name, game, day, price FROM m.price_history WHERE card_id IS NULL")
        c.execute("INSERT OR REPLACE INTO main.settings (key, value) "
                  "SELECT key, value FROM m.settings WHERE key IN ('fx_rates', 'catalog_updated')")
        c.commit()
    finally:
        c.execute("DETACH DATABASE m")
    DB.set_setting("market_built", built)
    _refresh_riftbound_values()
    _changed("all")


def _refresh_riftbound_values() -> None:
    """Riftbound cards in your collection and wishlist take the day's price."""
    for card in DB.search(wishlist=None):
        if not card.game or "riftbound" in card.game.lower():
            price = riftbound_price(card)
            if price and abs(price - card.value) > 0.001:
                card.value = price
                DB.update(card)


def summary() -> dict:
    return {
        "currency": currency.code(), "symbol": currency.symbol(), "rate": currency.describe(),
        "choices": list(currency.CHOICES), "currency_label": currency.label(),
        "ebay_site": _ebay_site(), "ebay_sites": EBAY_SITES,
        "catalog_updated": DB.get_setting("catalog_updated"),
        "market_built": DB.get_setting("market_built"),
        "decks": len(META.decks()), "printings": len(_catalog()),
        "topdeck": bool(META.known_sources("topdeck:")),
    }


def set_currency(code: str) -> dict:
    currency.set_currency(DB, currency.CHOICES.get(code, code))
    _changed("all")
    return summary()


def set_ebay_site(site: str) -> dict:
    DB.set_setting("ebay_site", site)
    return summary()


# --- collection --------------------------------------------------------------


def riftbound_price(card: Card) -> float:
    """A Riftbound card's price from the day's price list: the exact printing
    when its card number is known, otherwise the cheapest standard printing."""
    cards = _catalog()
    code = card.number.strip().upper()
    if code:
        for c in cards:
            if c.code.upper() == code and c.main_price:
                return c.main_price
    key = name_key(card.name)
    same = [c for c in cards if name_key(c.base_name) == key and c.main_price]
    standard = [c for c in same if c.version == "Standard"] or same
    return min((c.main_price for c in standard), default=0.0)


def _sync_fetch(url: str):
    """Price services, fetched from the browser (Pyodide has no sockets)."""
    from pyodide.http import open_url  # only exists on the phone
    return json.load(open_url(url))


def _lookup(card: Card) -> tuple[float, str]:
    """(US dollar price, where it came from)."""
    if not card.game or "riftbound" in card.game.lower():
        price = riftbound_price(card)
        if not price:
            raise pricing.PriceLookupError("Not found in today's Riftbound price list. Check the name or card number.")
        return price, "TCGplayer via riftbound.gg"
    if not pricing.supported(card):
        raise pricing.PriceLookupError("Set Game to Riftbound, Magic, Pokémon or Yu-Gi-Oh! to look up prices.")
    result = pricing.lookup_price(card, fetch=_sync_fetch)
    return result.price, f"{result.source}: {result.matched}"


def collection(view: str = "collection", search: str = "", game: str = "", sort: str = "", reverse: bool = False) -> dict:
    wishlist = view == "wishlist"
    cards = DB.search(search.strip(), game, wishlist=wishlist)
    if sort:
        cards.sort(key=lambda c: (lambda v: v.lower() if isinstance(v, str) else v)(getattr(c, sort)), reverse=reverse)
    owned, wanted = DB.stats(), DB.stats(wishlist=True)
    return {
        "games": DB.games(),
        "rows": [{"id": c.id, "name": c.name, "game": c.game, "set": c.set_name, "number": c.number,
                  "rarity": c.rarity, "condition": c.condition, "quantity": c.quantity,
                  "value": currency.fmt(c.value) if c.value else "", "photo": c.image_path == PHOTO,
                  "total": currency.fmt(c.value * c.quantity) if c.value else ""} for c in cards],
        "tiles": {"value": currency.fmt(owned["total_value"]), "cards": f"{owned['total_cards']:,}",
                  "unique": f"{owned['entries']:,}",
                  "wishlist": currency.fmt(wanted["total_value"])
                  + (f" · {wanted['total_cards']} cards" if wanted["total_cards"] else "")},
        "status": f"{len(cards)} {'entry' if len(cards) == 1 else 'entries'} · {sum(c.quantity for c in cards)} cards"
                  f" · {currency.fmt(sum(c.quantity * c.value for c in cards))}",
    }


def _form(card: Card) -> dict:
    return {
        "id": card.id, "name": card.name, "game": card.game, "set_name": card.set_name, "number": card.number,
        "rarity": card.rarity, "condition": card.condition, "quantity": card.quantity,
        "value": f"{currency.from_usd(card.value):.2f}" if card.value else "",
        "paid": f"{card.purchase_price:.2f}" if card.purchase_price else "",
        "notes": card.notes, "wishlist": card.wishlist, "photo": card.image_path == PHOTO,
        "symbol": currency.symbol(), "games": GAMES, "rarities": RARITIES, "conditions": CONDITIONS,
    }


def card(id: int | None = None) -> dict:
    found = DB.get(id) if id else None
    return _form(found or Card(name="", game="Riftbound"))


def card_details(id: int) -> dict:
    c = DB.get(id)
    if not c:
        raise ValueError("That card no longer exists.")
    where = " ".join(filter(None, (c.set_name, f"#{c.number}" if c.number else "")))
    history = DB.price_history(card_id=c.id)
    return {
        "id": c.id, "name": c.name, "photo": c.image_path == PHOTO, "wishlist": c.wishlist,
        "info": [x for x in (" · ".join(filter(None, (c.game, where))), " · ".join(filter(None, (c.rarity, c.condition))))
                 if x],
        "value": (currency.fmt(c.value) if c.quantity == 1
                  else f"{c.quantity} × {currency.fmt(c.value)} = {currency.fmt(c.quantity * c.value)}"),
        "paid": f"Paid {currency.fmt_local(c.purchase_price)} each" if c.purchase_price else "",
        "notes": c.notes, "history": _series(history, currency.from_usd), "added": c.date_added,
    }


def save_card(fields: dict) -> int:
    existing = DB.get(fields["id"]) if fields.get("id") else None
    base = existing or Card(name="")
    try:
        quantity = int(fields.get("quantity") or 0)
        value_text = str(fields.get("value") or "")
        # Values are kept in US dollars like looked-up prices; an unchanged value keeps its exact amount.
        value = (base.value if existing and value_text == fields.get("value_original")
                 else currency.to_usd(currency.parse(value_text)))
        paid = currency.parse(str(fields.get("paid") or ""))
    except ValueError:
        raise ValueError("Quantity must be a whole number, and value and paid must be numbers.")
    new = replace(base, name=str(fields.get("name", "")).strip(), game=fields.get("game", "").strip(),
                  set_name=fields.get("set_name", "").strip(), number=fields.get("number", "").strip(),
                  rarity=fields.get("rarity", "").strip(), condition=fields.get("condition", "").strip(),
                  quantity=quantity, value=value, purchase_price=paid, notes=fields.get("notes", "").strip(),
                  wishlist=bool(fields.get("wishlist")))
    if "photo" in fields:
        new.image_path = PHOTO if fields["photo"] else ""
    if existing:
        DB.update(new)
        card_id = new.id
    else:
        card_id = DB.add(new)
    _changed("cards")
    return card_id


def delete_cards(ids: list[int]) -> int:
    for i in ids:
        DB.delete(i)
    _changed("cards")
    return len(ids)


def move_cards(ids: list[int]) -> int:
    for i in ids:
        c = DB.get(i)
        if c:
            c.wishlist = not c.wishlist
            DB.update(c)
    _changed("cards")
    return len(ids)


def lookup_price(fields: dict) -> dict:
    c = Card(name=str(fields.get("name", "")).strip(), game=fields.get("game", ""),
             set_name=fields.get("set_name", ""), number=fields.get("number", ""),
             rarity=fields.get("rarity", ""), notes=fields.get("notes", ""))
    if not c.name:
        raise ValueError("Enter the card's name first.")
    price, source = _lookup(c)
    return {"value": f"{currency.from_usd(price):.2f}", "text": f"{currency.fmt(price)} from {source}"}


def update_prices(ids: list[int]) -> dict:
    cards = [c for c in (DB.get(i) for i in ids) if c]
    updated, failed = 0, []
    for c in cards:
        try:
            c.value, _ = _lookup(c)
            DB.update(c)
            updated += 1
        except Exception as e:
            failed.append(f"{c.name}: {e}")
    _changed("cards")
    return {"updated": updated, "total": len(cards), "failed": failed}


def export_csv() -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CARD_FIELDS)
    writer.writeheader()
    for c in DB.search(wishlist=None):
        row = {f: getattr(c, f) for f in CARD_FIELDS}
        row["wishlist"] = "yes" if c.wishlist else ""
        row["image_path"] = ""
        writer.writerow(row)
    return out.getvalue()


def import_csv(text: str) -> int:
    path = "/tmp/import.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    count = DB.import_csv(path)
    _changed("cards")
    return count


# --- meta tracker ------------------------------------------------------------


def _meta_filters(legend: str, since: str, top: str) -> tuple[str, str, int | None]:
    if since:
        try:
            date.fromisoformat(since)
        except ValueError:
            since = ""
    return ("" if legend in ("", "All legends") else legend), since, TOP_CHOICES.get(top)


def meta(legend: str = "", since: str = "", top: str = "All placements", runes: bool = False) -> dict:
    legend, since, top_n = _meta_filters(legend, since, top)
    decks = META.decks(legend, since, top_n)
    usage = META.card_usage(legend, since, top_n, include_runes=runes)
    shares = META.legend_shares(since, top_n)
    events = {d.event for d in decks if d.event}
    top_legend = shares[0] if shares and not legend else None
    total = len(META.decks())
    shown = f"{len(decks)} decklist{'s' if len(decks) != 1 else ''} from {len(events)} event{'s' if len(events) != 1 else ''}"
    return {
        "legends": ["All legends"] + META.legends(), "tops": list(TOP_CHOICES),
        "tiles": {
            "decks": f"{len(decks):,}", "events": f"{len(events):,}",
            "legend": (f"{top_legend.legend.split(',')[0].split(' - ')[0]} {top_legend.share:.0%}" if top_legend
                       else (legend.split(",")[0].split(" - ")[0] if legend else "—")),
            "short": f"{sum(1 for u in usage if u.owned < typical_copies(u.avg_copies))} of {len(usage)}",
        },
        "decks": [{"id": d.id, "date": d.date, "event": d.event, "player": d.player, "legend": d.legend,
                   "placement": d.placement or "", "mine": not d.source_id} for d in decks[:300]],
        "more_decks": max(0, len(decks) - 300),
        "usage": [{"name": u.name, "section": u.section, "decks": u.decks, "share": f"{u.share:.0%}",
                   "avg": f"{u.avg_copies:.1f}", "owned": u.owned,
                   "short": u.owned < typical_copies(u.avg_copies)} for u in usage],
        "shares": [{"legend": s.legend, "decks": s.decks, "share": f"{s.share:.0%}", "best": s.best_placement or ""}
                   for s in shares],
        "status": shown if len(decks) == total else f"{shown} (filtered from {total})",
        "topdeck": bool(META.known_sources("topdeck:")),
    }


def deck(id: int) -> dict:
    d = META.get_deck(id)
    if not d:
        raise ValueError("That decklist no longer exists.")
    owned = META.owned_counts()
    sections = []
    for section in SECTIONS:
        cards = [c for c in d.cards if c.section == section]
        if cards:
            sections.append({"name": section, "count": sum(c.quantity for c in cards), "cards": [
                {"qty": c.quantity, "name": c.name, "owned": owned.get(card_key(c.name), 0),
                 "short": owned.get(card_key(c.name), 0) < c.quantity and section != LEGEND} for c in cards]})
    record = (f"{d.wins}-{d.losses}" + (f"-{d.ties}" if d.ties else "")) if d.wins is not None and d.losses is not None else ""
    return {"id": d.id, "title": " – ".join(filter(None, [d.legend, d.player])) or "Deck",
            "header": " · ".join(filter(None, [d.date, d.event, f"Placed {d.placement}" if d.placement else "",
                                                f"{d.players} players" if d.players else "", record, d.name])),
            "sections": sections, "mine": not d.source_id}


def add_deck(text: str, name: str = "", player: str = "", event: str = "", placement: str = "", day: str = "") -> int:
    d = parse_decklist(text)
    d.name, d.player, d.event = name.strip(), player.strip(), event.strip()
    if placement.strip():
        try:
            d.placement = int(placement.strip().lstrip("#").rstrip("stndrdth"))
        except ValueError:
            raise ValueError("Placement must be a number, e.g. 1 or 8.")
    if day.strip():
        date.fromisoformat(day.strip())
        d.date = day.strip()
    deck_id = META.add_deck(d)
    _changed("all")
    return deck_id


def delete_decks(ids: list[int]) -> int:
    for i in ids:
        META.delete_deck(i)
    _changed("all")
    return len(ids)


def add_missing(names: list[str], legend: str = "", since: str = "", top: str = "All placements",
                runes: bool = False) -> str:
    legend, since, top_n = _meta_filters(legend, since, top)
    wanted = {card_key(n) for n in names}
    usage = [u for u in META.card_usage(legend, since, top_n, include_runes=runes) if card_key(u.name) in wanted]
    wishlist = {card_key(c.name): c for c in DB.search(wishlist=True) if not c.game or "riftbound" in card_key(c.game)}
    added = updated = 0
    for u in usage:
        missing = typical_copies(u.avg_copies) - u.owned
        if missing <= 0:
            continue
        existing = wishlist.get(card_key(u.name))
        if existing:
            if existing.quantity != missing:
                existing.quantity = missing
                DB.update(existing)
                updated += 1
        else:
            new = Card(name=u.name, game="Riftbound", quantity=missing, wishlist=True, notes="Added from meta tracker")
            new.value = riftbound_price(new)
            DB.add(new)
            added += 1
    _changed("cards")
    if not added and not updated:
        return "You already own enough of those cards."
    return f"Added {added} card{'s' if added != 1 else ''} to your wishlist" + (f" and updated {updated}" if updated else "") + "."


# --- market: all cards -------------------------------------------------------


def _sold_by_code() -> dict:
    return _cached("sold", "by_code", DB.sold_prices_by_code)


def _meta_text(trend) -> str:
    if trend is None or not trend.enough_data:
        return ""
    if abs(trend.change_points) < 0.5:
        return "0"
    return f"{trend.change_points:+.0f} pts" + ("" if trend.significant else "?")


def catalog_list(search: str = "", set_name: str = "", rarity: str = "", version: str = "", meta_filter: str = "",
                 min1: bool = False, days: int = 7, sort: str = "price", reverse: bool = True,
                 limit: int = 150) -> dict:
    cards = _catalog()
    play, trends, owned, sold = _play(), _trends(days), META.owned_counts(), _sold_by_code()
    text = search.strip().lower()

    def meta_ok(c) -> bool:
        k = name_key(c.base_name)
        if meta_filter in ("", META_FILTERS[0]):
            return True
        if meta_filter == "Played in meta":
            return k in play
        if meta_filter == "Not played":
            return k not in play
        t = trends.get(k)
        if t is None or not t.significant:
            return False
        return t.change_points >= market.RISING_POINTS if meta_filter == "Rising in meta" else t.change_points <= market.FALLING_POINTS

    shown = [c for c in cards
             if (not text or text in c.name.lower() or text in c.code.lower())
             and (not set_name or c.set_name == set_name) and (not rarity or c.rarity == rarity)
             and (not version or c.version == version)
             and (not min1 or currency.from_usd(c.main_price) >= 1) and meta_ok(c)]

    def sold_of(c):
        return market.recent_sold(sold.get(c.code.upper(), []))

    def value(c):
        k = name_key(c.base_name)
        t = trends.get(k)
        return {
            "name": c.base_name.lower(), "number": c.number_key(),
            "version": (catalog.VERSIONS.index(c.version) if c.version in catalog.VERSIONS else 99, c.detail),
            "price": c.main_price or None, "ebay": (sold_of(c) or (None,))[0], "d1": c.change_pct(1),
            "d7": c.change_pct(7), "cm": c.cm_price or None, "play": play.get(k, 0.0), "owned": owned.get(k, 0),
            "meta": t.change_points if t is not None and t.enough_data else None,
        }[sort]

    known = [c for c in shown if value(c) is not None]
    unknown = [c for c in shown if value(c) is None]
    known.sort(key=value, reverse=reverse)
    shown = known + unknown
    rows = []
    for c in shown[:limit]:
        k = name_key(c.base_name)
        d7 = c.change_pct(7)
        s = sold_of(c)
        rows.append({
            "code": c.code, "name": c.base_name, "version": c.version,
            "price": (currency.fmt(c.main_price) + (" F" if c.is_foil_only else "")) if c.main_price else "—",
            "ebay": "" if not s else currency.fmt_local(s[0]) + (f" ({s[1]})" if s[1] > 1 else ""),
            "d1": _pct(c.change_pct(1)), "d7": _pct(d7),
            "trend": "up" if d7 and d7 >= 0.05 else ("down" if d7 and d7 <= -0.05 else ""),
            "cm": currency.fmt_eur(c.cm_price) if c.cm_price else "",
            "play": f"{play[k]:.0%}" if k in play else "", "meta": _meta_text(trends.get(k)),
            "owned": owned.get(k) or "",
        })
    note = ""
    if trends and not any(t.enough_data for t in trends.values()):
        note = "Meta move needs more decklists in the earlier period; try another Compare period."
    return {
        "rows": rows, "shown": len(shown), "total": len(cards), "note": note,
        "sets": sorted({c.set_name for c in cards if c.set_name}),
        "rarities": sorted({c.rarity for c in cards if c.rarity}),
        "versions": catalog.VERSIONS, "meta_filters": META_FILTERS, "windows": WINDOWS,
        "updated": _friendly_day(DB.get_setting("catalog_updated")) if DB.get_setting("catalog_updated") else "",
    }


def _find(code: str):
    for c in _catalog():
        if c.code.upper() == code.upper():
            return c
    raise ValueError(f"Card {code} isn't in the price list.")


def catalog_card(code: str, days: int = 7) -> dict:
    c = _find(code)
    k = name_key(c.base_name)
    play, owned = _play(), META.owned_counts()
    facts = []
    if c.price and c.foil_price:
        facts.append(f"Foil {currency.fmt(c.foil_price)}")
    for label, n in (("1 day", 1), ("7 days", 7)):
        if c.change_pct(n) is not None:
            delta = c.change_1d if n == 1 else c.change_7d
            facts.append(f"{label}: no change" if abs(delta) < 0.005 else
                         f"{label}: {_pct(c.change_pct(n))} ({'+' if delta >= 0 else '−'}{currency.fmt(abs(delta))})")
    if c.cm_price:
        facts.append(f"EU price (Cardmarket) {currency.fmt_eur(c.cm_price)}"
                     + (f" (€{c.cm_price:,.2f})" if currency.code() != "EUR" else ""))
    s = market.recent_sold(_sold_by_code().get(c.code.upper(), []))
    if s:
        avg, count, newest = s
        text = (f"eBay sold: {currency.fmt_local(avg)}" + (f" (average of {count})" if count > 1 else "")
                + f", latest {_friendly_day(newest).lower()}")
        if c.main_price:
            diff = (currency.to_usd(avg) - c.main_price) / c.main_price
            if abs(diff) >= 0.05:
                text += f", {abs(diff):.0%} {'above' if diff > 0 else 'below'} TCGplayer"
        facts.append(text)
    else:
        facts.append("eBay sold: none logged yet")
    facts.append(f"Played in {play[k]:.0%} of decklists" if k in play else "Not in your decklists")
    t = _trends(days).get(k)
    if t is not None and t.enough_data:
        facts.append(f"Meta move: {t.previous_share:.0%} → {t.recent_share:.0%} of decklists "
                     f"({t.change_points:+.0f} pts, last {days} days vs the {days} before"
                     + (")" if t.significant else ", could be chance)"))
    if owned.get(k):
        facts.append(f"You own {owned[k]}")
    series = market.play_rate_series(META, c.base_name)
    while series and series[0][1] is None:
        series.pop(0)
    if not any(v >= 0.005 for _, v in series if v is not None):
        series = []
    words = [c.base_name.replace(" - ", " ")]
    if c.version in ("Alternate art", "Overnumbered", "Signature"):
        words.append(c.version.lower())
    return {
        "code": c.code, "name": c.base_name, "version": c.version,
        "info": " · ".join(filter(None, [c.code, c.version, c.set_name, c.rarity, c.card_type])),
        "detail": c.detail,
        "price": (currency.fmt(c.main_price) + ("  foil" if c.is_foil_only else "")) if c.main_price else "No price",
        "facts": facts, "image": c.image,
        "history": _series(catalog.history(DB, c), currency.from_usd),
        "play": _series(series),
        "ebay_url": market.ebay_sold_url(" ".join(words), "Riftbound", _ebay_site()),
    }


def _catalog_card_fields(c, wishlist: bool) -> Card:
    notes = ", ".join(filter(None, [c.version if c.version != "Standard" else "", c.detail,
                                    "Foil" if c.is_foil_only else ""]))
    return Card(name=c.base_name, game="Riftbound", set_name=c.set_name, number=c.code, rarity=c.rarity,
                value=c.main_price, wishlist=wishlist, notes=notes)


def catalog_form(code: str) -> dict:
    """The Add card form, filled in from a printing."""
    return _form(_catalog_card_fields(_find(code), False))


def catalog_wishlist(code: str) -> str:
    c = _find(code)
    if any(w.number.upper() == c.code.upper() for w in DB.search(wishlist=True)):
        return f"{c.base_name} ({c.code}) is already on your wishlist."
    DB.add(_catalog_card_fields(c, True))
    _changed("cards")
    return f"Added {c.base_name} ({c.code}) to your wishlist."


# --- market: signals -----------------------------------------------------------


def _rows(days: int) -> list:
    return _cached("cards", ("rows", days), lambda: market.analyse(DB, META, days=days))


def _recent_sold(r):
    if r.card and r.card.number:
        s = market.recent_sold(DB.sold_prices(r.name, r.card.number))
        if s:
            return s
    return market.recent_sold(DB.sold_prices(r.name))


def signals(view: str = "mine", days: int = 7, signal: str = "All signals", sort: str = "", reverse: bool = True) -> dict:
    rows = _rows(days)
    wanted = SIGNAL_FILTERS.get(signal)
    shown = [r for r in rows if (view == "all" or (view == "mine") == (r.owned > 0))
             and (wanted is None or r.signal in wanted)]

    def value(r):
        known = r.trend is not None and r.trend.enough_data
        if sort == "name":
            return r.name.lower()
        if sort == "ebay":
            s = _recent_sold(r)
            return s[0] if s else None
        return {"owned": r.owned, "value": r.value or None, "profit": r.profit,
                "price": r.price_change.fraction if r.price_change else None,
                "play": r.trend.recent_share if r.trend else None,
                "meta": r.trend.change_points if known else None}.get(sort)

    if sort:
        known = [r for r in shown if value(r) is not None]
        unknown = [r for r in shown if value(r) is None]
        known.sort(key=value, reverse=reverse)
        shown = known + unknown
    out = []
    for r in shown:
        s = _recent_sold(r)
        known = r.trend is not None and r.trend.enough_data
        out.append({
            "name": r.name, "signal": r.signal, "tag": SIGNAL_TAGS[r.signal], "owned": r.owned or "",
            "value": currency.fmt(r.value) if r.value else "",
            "ebay": (currency.fmt_local(s[0]) + (f" ({s[1]})" if s[1] > 1 else "")) if s else "",
            "profit": (("+" if r.profit >= 0 else "−") + currency.fmt(abs(r.profit))) if r.profit is not None else "",
            "price": f"{r.price_change.fraction:+.0%} ({r.price_change.days}d)" if r.price_change else "",
            "play": f"{r.trend.recent_share:.0%}" if r.trend else "",
            "meta": (f"{r.trend.change_points:+.0f} pts" + ("" if r.trend.significant else "?")) if known
            else ("few decks" if r.trend else ""),
        })
    profits = [r.profit for r in rows if r.profit is not None]
    anchor = market.meta_anchor(META)
    last = DB.last_price_update() or DB.get_setting("catalog_updated")
    return {
        "rows": out, "signals": list(SIGNAL_FILTERS), "windows": WINDOWS,
        "tiles": {
            "profit": (("+" if sum(profits) >= 0 else "−") + currency.fmt(abs(sum(profits)))) if profits else "Add prices paid",
            "sell": str(sum(1 for r in rows if SIGNAL_TAGS[r.signal] == "sell")),
            "buy": str(sum(1 for r in rows if r.signal == market.BUY_EARLY)),
            "updated": _friendly_day(last) if last else "Never",
        },
        "status": (f"{len(out)} card{'s' if len(out) != 1 else ''} · meta move: last {days} days of decklists"
                   f"{f' (to {anchor:%d %b})' if anchor else ''} vs the {days} before · "
                   "\"?\" = could be chance · signals are rules of thumb, not guarantees"),
    }


def signal_card(name: str, days: int = 7) -> dict:
    r = next((x for x in _rows(days) if x.name == name), None)
    if r is None:
        raise ValueError(f"{name} isn't in this list any more.")
    facts = []
    if r.owned:
        facts.append(f"You own {r.owned}")
        if r.owned > market.MAX_COPIES:
            facts.append(f"{r.owned - market.MAX_COPIES} spare beyond a playset")
    if r.value:
        facts.append(f"worth {currency.fmt(r.value)} each")
    if r.paid:
        facts.append(f"paid {currency.fmt(r.paid)}")
    s = _recent_sold(r)
    if not s:
        ebay = "eBay sold: none logged. Open eBay's sold listings and log a few prices."
    else:
        avg, count, newest = s
        ebay = (f"eBay sold: {currency.fmt_local(avg)}" + (f" (average of {count})" if count > 1 else "")
                + f", latest {_friendly_day(newest).lower()}.")
        if r.value:
            diff = (currency.to_usd(avg) - r.value) / r.value
            if abs(diff) >= 0.05:
                ebay += (f" {abs(diff):.0%} {'above' if diff > 0 else 'below'} TCGplayer"
                         + (", so eBay may pay more." if diff > 0 else "."))
    history = DB.price_history(card_id=r.card.id) if r.card else []
    if len(history) < 2:
        history = DB.price_history(name=r.name) or history
    game = r.card.game if r.card else "Riftbound"
    return {
        "name": r.name, "signal": r.signal if r.signal != market.NO_SIGNAL else "No signal",
        "tag": SIGNAL_TAGS[r.signal], "reason": r.reason, "facts": " · ".join(facts), "ebay": ebay,
        "history": _series(history, currency.from_usd), "play": _series(market.play_rate_series(META, r.name)),
        "ebay_url": market.ebay_sold_url(r.name, game, _ebay_site()),
        "code": r.card.number if r.card else "",
    }


# --- sold prices --------------------------------------------------------------


def sold(name: str, code: str | None = None) -> dict:
    rows = DB.sold_prices(name, code or None)
    return {"symbol": currency.symbol(), "label": currency.label(), "today": date.today().isoformat(), "rows": [
        {"id": r["id"], "day": r["day"], "price": currency.fmt_local(r["price"]), "note": r["note"],
         "code": r["code"] if r["code"] and not code else ""} for r in rows]}


def sold_add(name: str, price: str, day: str = "", note: str = "", code: str = "") -> dict:
    try:
        value = currency.parse(price)
        day = date.fromisoformat((day or date.today().isoformat()).strip()).isoformat()
        DB.add_sold_price(name, value, day, note, code=code or "")
    except ValueError:
        raise ValueError("Enter a price above zero and a date like 2026-09-27.")
    _changed("sold", "cards")
    return sold(name, code or None)


def sold_delete(id: int, name: str, code: str = "") -> dict:
    DB.delete_sold_price(id)
    _changed("sold", "cards")
    return sold(name, code or None)


# --- future insight -------------------------------------------------------------


def _bar(score: float) -> str:
    filled = round(score / 20)
    return "▮" * filled + "▯" * (5 - filled)


def insight_report(weeks: str = "Last 6 weeks", view: str = "all") -> dict:
    n = LOOK_BACK.get(weeks, 6)
    r = _cached("cards", ("insight", n), lambda: insight.analyse(DB, META, weeks=n))
    shown = [c for c in r.candidates if view == "all" or (view == "owned") == (c.owned > 0)]
    legends = []
    for t in r.legends:
        trend = "Rising" if t.rising else ("Falling" if t.change_points <= -5 and t.z <= -insight.Z_NEEDED else "Steady")
        legends.append({"legend": t.legend, "share": f"{t.previous_share:.0%} → {t.recent_share:.0%}",
                        "change": f"{t.change_points:+.0f} pts", "trend": trend})
    return {
        "weeks": list(LOOK_BACK),
        "tiles": {"watch": str(sum(1 for c in r.candidates if c.score >= 30)),
                  "legends": str(sum(1 for t in r.legends if t.rising)),
                  "decks": f"{r.decks:,}", "data": f"{r.with_placing:,} / {r.with_record:,}"},
        "rows": [{"name": c.name, "score": round(c.score), "bar": _bar(c.score), "tags": c.tags,
                  "tier": "hot" if c.score >= 50 else ("warm" if c.score >= 30 else "mild"),
                  "play": f"{c.share:.0%}" if c.share else "not played",
                  "top": f"{c.top_share:.0%}" if c.top_share is not None else "",
                  "win": f"{c.win_rate:.0%}" if c.win_rate is not None else "", "owned": c.owned or "",
                  "signs": [{"kind": s.kind, "detail": s.detail} for s in sorted(c.signs, key=lambda s: -s.points)],
                  "ebay_url": market.ebay_sold_url(c.name, "Riftbound", _ebay_site())} for c in shown],
        "legends": legends,
        "status": r.message or (f"Based on {r.decks} decklists from {r.start} to {r.end}. These are early warning "
                                "signs to research, not predictions, and can be wrong."),
    }


def wishlist_names(names: list[str], note: str = "") -> str:
    have = {name_key(c.name) for c in DB.search(wishlist=True)}
    added = 0
    for n in names:
        if name_key(n) in have:
            continue
        new = Card(name=n, game="Riftbound", wishlist=True, notes=note)
        new.value = riftbound_price(new)
        DB.add(new)
        added += 1
    _changed("cards")
    return (f"Added {added} card{'s' if added != 1 else ''} to your wishlist." if added
            else "Those cards are already on your wishlist.")


API = {f.__name__: f for f in [
    open_db, summary, set_currency, set_ebay_site,
    collection, card, card_details, save_card, delete_cards, move_cards, lookup_price, update_prices,
    export_csv, import_csv,
    meta, deck, add_deck, delete_decks, add_missing,
    catalog_list, catalog_card, catalog_form, catalog_wishlist,
    signals, signal_card, sold, sold_add, sold_delete,
    insight_report, wishlist_names,
]}
