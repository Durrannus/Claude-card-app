"""Look up current market prices from free, public card databases.

Supported games (matched on the card's "game" field, ignoring case):

- Magic: The Gathering -> Scryfall (https://scryfall.com/docs/api)
- Pokémon              -> Pokémon TCG API (https://pokemontcg.io)
- Yu-Gi-Oh!            -> YGOPRODeck (https://ygoprodeck.com/api-guide/)

Prices are in US dollars, based on TCGplayer market data.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .db import Card

USER_AGENT = "CardCollectionLogger/1.0"
TIMEOUT = 15


class PriceLookupError(Exception):
    """A lookup failed; the message is suitable for showing to the user."""


@dataclass
class PriceResult:
    price: float
    source: str
    matched: str  # description of the printing that was priced


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def detect_source(game: str) -> str | None:
    g = _norm(game)
    if not g:
        return None
    if g in ("mtg", "magic") or "magicthegathering" in g:
        return "scryfall"
    if "pokemon" in g or "pokmon" in g:
        return "pokemon"
    if "yugioh" in g:
        return "yugioh"
    return None


def supported(card: Card) -> bool:
    return detect_source(card.game) is not None


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            return {}  # these APIs answer "no such card" with 400/404
        raise PriceLookupError(f"Price service returned an error ({e.code}).") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise PriceLookupError(
            "Could not reach the price service. Check your internet connection."
        ) from e
    except json.JSONDecodeError as e:
        raise PriceLookupError("Price service sent an unexpected response.") from e


def lookup_price(card: Card, fetch=_get_json) -> PriceResult:
    """Return the current market price for `card`.

    Raises PriceLookupError if the game isn't supported, the card can't be
    found, or no price is listed for it.
    """
    source = detect_source(card.game)
    if source is None:
        raise PriceLookupError(
            f"Price lookup isn't available for game '{card.game or '(none)'}'. "
            "Supported: Magic, Pokémon, Yu-Gi-Oh!"
        )
    if not card.name.strip():
        raise PriceLookupError("Card has no name.")
    handler = {"scryfall": _scryfall, "pokemon": _pokemon, "yugioh": _yugioh}[source]
    result = handler(card, fetch)
    if result is None:
        raise PriceLookupError(f"No price found for '{card.name}'.")
    return result


# --- Magic: Scryfall -------------------------------------------------------


def _scryfall(card: Card, fetch) -> PriceResult | None:
    query = f'!"{card.name.strip()}"'
    url = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": query, "unique": "prints", "order": "released"}
    )
    prints = fetch(url).get("data", [])
    if not prints:
        return None
    return parse_scryfall(prints, card)


def parse_scryfall(prints: list[dict], card: Card) -> PriceResult | None:
    want_foil = "foil" in card.notes.lower() or "foil" in card.rarity.lower()

    def price_of(p: dict) -> float | None:
        prices = p.get("prices") or {}
        order = ("usd_foil", "usd_etched", "usd") if want_foil else ("usd", "usd_foil", "usd_etched")
        for key in order:
            if prices.get(key):
                return float(prices[key])
        return None

    candidates = [p for p in prints if price_of(p) is not None]
    if not candidates:
        return None
    best = _best_match(
        candidates,
        card,
        set_names=lambda p: (p.get("set_name", ""), p.get("set", "")),
        number=lambda p: p.get("collector_number", ""),
    )
    return PriceResult(
        price=price_of(best),
        source="Scryfall",
        matched=f"{best.get('name')} — {best.get('set_name')} #{best.get('collector_number')}",
    )


# --- Pokémon: pokemontcg.io -----------------------------------------------

POKEMON_PRICE_ORDER = [
    "normal",
    "holofoil",
    "reverseHolofoil",
    "1stEditionHolofoil",
    "1stEditionNormal",
    "unlimitedHolofoil",
    "unlimited",
]


def _pokemon(card: Card, fetch) -> PriceResult | None:
    name = card.name.strip().replace('"', "")
    url = "https://api.pokemontcg.io/v2/cards?" + urllib.parse.urlencode(
        {"q": f'name:"{name}"', "pageSize": 250}
    )
    results = fetch(url).get("data", [])
    if not results:
        return None
    return parse_pokemon(results, card)


def parse_pokemon(results: list[dict], card: Card) -> PriceResult | None:
    hints = f"{card.rarity} {card.notes}".lower()
    order = list(POKEMON_PRICE_ORDER)
    if "1st" in hints or "first edition" in hints:
        order = ["1stEditionHolofoil", "1stEditionNormal"] + order
    if "reverse" in hints:
        order.insert(0, "reverseHolofoil")
    elif "holo" in hints:
        order.remove("holofoil")
        order.insert(0, "holofoil")

    def price_of(c: dict) -> tuple[float, str] | None:
        prices = (c.get("tcgplayer") or {}).get("prices") or {}
        for variant in order + [k for k in prices if k not in order]:
            p = prices.get(variant) or {}
            value = p.get("market") or p.get("mid")
            if value:
                return float(value), variant
        return None

    candidates = [c for c in results if price_of(c) is not None]
    if not candidates:
        return None
    best = _best_match(
        candidates,
        card,
        set_names=lambda c: ((c.get("set") or {}).get("name", ""), (c.get("set") or {}).get("id", "")),
        number=lambda c: c.get("number", ""),
    )
    price, variant = price_of(best)
    return PriceResult(
        price=price,
        source="Pokémon TCG API",
        matched=f"{best.get('name')} — {(best.get('set') or {}).get('name')} #{best.get('number')} ({variant})",
    )


# --- Yu-Gi-Oh!: YGOPRODeck -------------------------------------------------


def _yugioh(card: Card, fetch) -> PriceResult | None:
    url = "https://db.ygoprodeck.com/api/v7/cardinfo.php?" + urllib.parse.urlencode(
        {"fname": card.name.strip()}
    )
    results = fetch(url).get("data", [])
    if not results:
        return None
    return parse_yugioh(results, card)


def parse_yugioh(results: list[dict], card: Card) -> PriceResult | None:
    wanted = _norm(card.name)
    exact = [r for r in results if _norm(r.get("name", "")) == wanted]
    entry = (exact or results)[0]

    # A specific printing, when the set or set code (e.g. "LOB-EN005") matches.
    sets = entry.get("card_sets") or []
    if card.set_name or card.number:
        for s in sets:
            code_match = card.number and _norm(s.get("set_code", "")) == _norm(card.number)
            set_match = card.set_name and _norm(s.get("set_name", "")) == _norm(card.set_name)
            price = _to_float(s.get("set_price"))
            if (code_match or set_match) and price:
                return PriceResult(
                    price=price,
                    source="YGOPRODeck",
                    matched=f"{entry.get('name')} — {s.get('set_name')} {s.get('set_code')}",
                )

    prices = (entry.get("card_prices") or [{}])[0]
    price = _to_float(prices.get("tcgplayer_price"))
    if not price:
        return None
    return PriceResult(price=price, source="YGOPRODeck", matched=f"{entry.get('name')} (any printing)")


def _to_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _best_match(candidates: list[dict], card: Card, set_names, number) -> dict:
    """Pick the printing that best matches the card's set and number."""
    want_set = _norm(card.set_name)
    want_num = _norm(card.number)

    def score(c: dict) -> int:
        s = 0
        names = [_norm(n) for n in set_names(c) if n]
        if want_set and want_set in names:
            s += 2
        elif want_set and any(want_set in n or n in want_set for n in names):
            s += 1
        if want_num and _norm(number(c)) == want_num:
            s += 2
        return s

    # max() keeps the first of equal scores, so unmatched cards fall back
    # to the API's own ordering.
    return max(candidates, key=score)
