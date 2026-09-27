"""Showing prices in your own currency (pounds by default).

Market prices arrive in US dollars (TCGplayer, Scryfall, Pokémon TCG,
YGOPRODeck) and euros (Cardmarket), and are stored that way, so the price
history stays consistent. They're converted when shown, at the day's
exchange rate from the European Central Bank (via frankfurter.dev, with
open.er-api.com as a fallback).

What you type yourself (what you paid, eBay sold prices) is kept in your
currency exactly as entered, so "paid £40" always shows as £40. Card values
typed in the collection form are converted to dollars like looked-up ones.
"""

import json
import urllib.request
from datetime import date

from .db import CardDatabase

SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
CHOICES = {"£ GBP": "GBP", "$ USD": "USD", "€ EUR": "EUR"}
DEFAULT = "GBP"
# Rough rates per US dollar, only used until the first download.
FALLBACK = {"USD": 1.0, "GBP": 0.75, "EUR": 0.87}

SOURCES = [
    ("European Central Bank via frankfurter.dev", "https://api.frankfurter.dev/v1/latest?from=USD&to=GBP,EUR"),
    ("ExchangeRate-API (open.er-api.com)", "https://open.er-api.com/v6/latest/USD"),
]

_state = {"code": DEFAULT, "rates": dict(FALLBACK), "day": "", "source": ""}


class RateError(Exception):
    """Exchange rates couldn't be downloaded; the message suits the user."""


# --- settings ----------------------------------------------------------------


def configure(db: CardDatabase) -> None:
    """Load the chosen currency and the last downloaded rates."""
    code = db.get_setting("currency", DEFAULT)
    _state["code"] = code if code in SYMBOLS else DEFAULT
    try:
        saved = json.loads(db.get_setting("fx_rates", "{}"))
    except ValueError:
        saved = {}
    rates = saved.get("rates") or {}
    _state["rates"] = {c: float(rates.get(c) or FALLBACK[c]) for c in SYMBOLS}
    _state["day"] = saved.get("day", "")
    _state["source"] = saved.get("source", "")
    if not db.get_setting("amounts_currency"):
        # Amounts typed before currencies existed were most likely in the
        # currency being chosen now, so they're kept as they are.
        db.set_setting("amounts_currency", _state["code"])


def code() -> str:
    return _state["code"]


def symbol() -> str:
    return SYMBOLS[_state["code"]]


def label() -> str:
    return f"{symbol()} {code()}"


def ebay_site() -> str:
    """The eBay site to open unless another was picked."""
    return "ebay.co.uk" if code() == "GBP" else "ebay.com"


def set_currency(db: CardDatabase, new: str) -> None:
    """Switch currency, converting what you paid and eBay sold prices so they
    keep the same worth."""
    if new not in SYMBOLS:
        raise ValueError(f"Unknown currency {new}")
    old = db.get_setting("amounts_currency", _state["code"])
    if old != new and old in SYMBOLS:
        factor = _state["rates"][new] / _state["rates"][old]
        db.conn.execute("UPDATE cards SET purchase_price = ROUND(purchase_price * ?, 2) WHERE purchase_price > 0",
                        (factor,))
        db.conn.execute("UPDATE sold_prices SET price = ROUND(price * ?, 2)", (factor,))
    db.set_setting("amounts_currency", new)
    db.set_setting("currency", new)  # commits
    _state["code"] = new


# --- conversion --------------------------------------------------------------


def from_usd(usd: float) -> float:
    return usd * _state["rates"][_state["code"]]


def to_usd(amount: float) -> float:
    return amount / _state["rates"][_state["code"]]


def from_eur(eur: float) -> float:
    return eur / _state["rates"]["EUR"] * _state["rates"][_state["code"]]


def fmt_local(amount: float) -> str:
    """An amount already in your currency, e.g. £1,234.56."""
    sign = "−" if amount < 0 else ""
    return f"{sign}{symbol()}{abs(amount):,.2f}"


def fmt(usd: float) -> str:
    """A US dollar price shown in your currency."""
    return fmt_local(from_usd(usd))


def fmt_eur(eur: float) -> str:
    """A euro price shown in your currency."""
    return fmt_local(from_eur(eur))


def parse(text: str) -> float:
    """A typed amount, ignoring currency signs and thousands separators."""
    for ch in "£$€, ":
        text = text.replace(ch, "")
    return float(text or 0)


def describe() -> str:
    """Where the current rate came from, for status lines."""
    if code() == "USD":
        return "prices in US dollars"
    if not _state["day"]:
        return f"approximate rate $1 = {fmt_local(from_usd(1))} (couldn't download today's yet)"
    return f"$1 = {symbol()}{from_usd(1):.4f} on {_state['day']}"


# --- downloading rates -------------------------------------------------------


def _default_fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "CardCollectionLogger/1.0",
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_rates(fetch=None) -> tuple[dict[str, float], str, str]:
    """(rates per US dollar, the rates' date, source name)."""
    fetch = fetch or _default_fetch
    problems = []
    for name, url in SOURCES:
        try:
            data = fetch(url)
            rates = data.get("rates") or {}
            found = {"USD": 1.0, "GBP": float(rates["GBP"]), "EUR": float(rates["EUR"])}
            if not all(v > 0 for v in found.values()):
                raise ValueError("zero rate")
            day = str(data.get("date") or date.today().isoformat())
            return found, day, name
        except Exception as e:  # try the next source
            problems.append(f"{name}: {e}")
    raise RateError("Couldn't download exchange rates (" + "; ".join(problems) + ").")


def rates_due(db: CardDatabase) -> bool:
    """True when the rates weren't downloaded today."""
    try:
        return json.loads(db.get_setting("fx_rates", "{}")).get("checked") != date.today().isoformat()
    except ValueError:
        return True


def save_rates(db: CardDatabase, fetched: tuple[dict[str, float], str, str]) -> str:
    """Save what fetch_rates returned (on the thread that owns `db`); returns describe()."""
    rates, day, source = fetched
    db.set_setting("fx_rates", json.dumps({"rates": rates, "day": day, "source": source,
                                           "checked": date.today().isoformat()}))
    configure(db)
    return describe()


def update_rates(db: CardDatabase, fetch=None) -> str:
    """Download and save today's rates; returns describe()."""
    return save_rates(db, fetch_rates(fetch))
