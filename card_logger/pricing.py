"""Look up current market prices from free, public card databases.

Supported games (matched on the card's "game" field, ignoring case):

- Riftbound            -> TCGplayer prices via TCGCSV (https://tcgcsv.com)
- Magic: The Gathering -> Scryfall (https://scryfall.com/docs/api)
- Pokémon              -> Pokémon TCG API (https://pokemontcg.io)
- Yu-Gi-Oh!            -> YGOPRODeck (https://ygoprodeck.com/api-guide/)

Prices are in US dollars, based on TCGplayer market data.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .db import Card

USER_AGENT = "CardCollectionLogger/1.0"
TIMEOUT = 30
CACHE_SECONDS = 60 * 60  # prices update daily, so an hour-old answer is fine

RIFTBOUND_CATEGORY = 89  # TCGplayer's category id for Riftbound
TCGCSV = f"https://tcgcsv.com/tcgplayer/{RIFTBOUND_CATEGORY}"


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
    if "riftbound" in g or "leagueoflegends" in g:
        return "riftbound"
    if g in ("mtg", "magic") or "magicthegathering" in g:
        return "scryfall"
    if "pokemon" in g or "pokmon" in g:
        return "pokemon"
    if "yugioh" in g:
        return "yugioh"
    return None


def supported(card: Card) -> bool:
    return detect_source(card.game) is not None


TEMPORARY_ERRORS = (429, 500, 502, 503, 504)
RETRY_DELAYS = (2, 5)  # seconds to wait before each retry of a temporary error


def _get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return {}  # these APIs answer "no such card" with 400/404
            if e.code in TEMPORARY_ERRORS and attempt < len(RETRY_DELAYS):
                time.sleep(RETRY_DELAYS[attempt])  # busy or briefly down: try again
                continue
            raise PriceLookupError(f"Price service returned an error ({e.code}). Try again later.") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise PriceLookupError(
                "Could not reach the price service. Check your internet connection."
            ) from e
        except json.JSONDecodeError as e:
            raise PriceLookupError("Price service sent an unexpected response.") from e


_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _cached_get_json(url: str) -> dict:
    """_get_json, remembering answers for CACHE_SECONDS so that pricing many
    cards from the same set only downloads that set once."""
    with _cache_lock:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    data = _get_json(url)
    with _cache_lock:
        _cache[url] = (time.monotonic(), data)
    return data


def lookup_price(card: Card, fetch=_cached_get_json) -> PriceResult:
    """Return the current market price for `card`.

    Raises PriceLookupError if the game isn't supported, the card can't be
    found, or no price is listed for it.
    """
    source = detect_source(card.game)
    if source is None:
        raise PriceLookupError(
            f"Price lookup isn't available for game '{card.game or '(none)'}'. "
            "Supported: Riftbound, Magic, Pokémon, Yu-Gi-Oh!"
        )
    if not card.name.strip():
        raise PriceLookupError("Card has no name.")
    handler = {"riftbound": _riftbound, "scryfall": _scryfall, "pokemon": _pokemon, "yugioh": _yugioh}[source]
    result = handler(card, fetch)
    if result is None:
        raise PriceLookupError(f"No price found for '{card.name}'.")
    return result


# --- Riftbound: TCGplayer data via TCGCSV ---------------------------------

VARIANT_WORDS = ("showcase", "alternate", "alt art", "overnumbered", "signature", "promo", "prerelease")


def _card_number(text: str) -> int | None:
    """'OGN-001/298', '001/298', '1' and 'OGN 001' all give 1."""
    digits = re.findall(r"\d+", text.split("/")[0])
    return int(digits[-1]) if digits else None


def _set_code(text: str) -> str:
    """'OGN-001' gives 'ogn'."""
    m = re.match(r"\s*([A-Za-z]{2,5})[\s-]*\d", text)
    return m.group(1).lower() if m else ""


def _riftbound(card: Card, fetch) -> PriceResult | None:
    groups = fetch(f"{TCGCSV}/groups").get("results", [])
    if not groups:
        raise PriceLookupError("Couldn't load the Riftbound set list.")
    want_set = _norm(card.set_name)
    want_code = _set_code(card.number)

    def matches(g: dict) -> bool:
        name, abbr = _norm(g.get("name", "")), _norm(g.get("abbreviation") or "")
        if want_code and abbr == want_code:
            return True
        return bool(want_set) and (want_set in (name, abbr) or (len(want_set) > 3 and want_set in name))

    # Look in the card's own set first; if it isn't found there (or no set
    # was given), look through every set, newest first.
    chosen = [g for g in groups if matches(g)]
    newest_first = sorted(groups, key=lambda g: g.get("publishedOn") or "", reverse=True)
    for batch in ([chosen] if chosen else []) + [[g for g in newest_first if g not in chosen]]:
        data = []
        for g in batch:
            gid = g["groupId"]
            data.append((
                g,
                fetch(f"{TCGCSV}/{gid}/products").get("results", []),
                fetch(f"{TCGCSV}/{gid}/prices").get("results", []),
            ))
        result = parse_riftbound(data, card)
        if result:
            return result
    return None


def parse_riftbound(data: list[tuple[dict, list[dict], list[dict]]], card: Card) -> PriceResult | None:
    """`data` holds (group, products, prices) for each set to search."""
    wanted = _norm(card.name)
    want_num = _card_number(card.number) if card.number else None
    hints = f"{card.rarity} {card.notes}".lower()
    want_variant = any(w in hints for w in VARIANT_WORDS)
    # A named version ("signature", "overnumbered", "alt art") picks that exact printing;
    # they can differ in price by a factor of ten.
    named = {("alternate" if w == "alt art" else w) for w in VARIANT_WORDS if w in hints and w != "showcase"}
    want_foil = "foil" in hints

    candidates = []
    for group, products, prices in data:
        by_product: dict[int, list[dict]] = {}
        for price in prices:
            by_product.setdefault(price.get("productId"), []).append(price)
        for product in products:
            extended = {e.get("name"): e.get("value") for e in product.get("extendedData") or []}
            if "Number" not in extended:
                continue  # booster boxes and other sealed products
            # "name" keeps the "(Alternate Art)"-style suffix in brackets.
            name = product.get("name") or product.get("cleanName", "")
            base = _norm(re.sub(r"\(.*?\)", "", name))
            full = _norm(name)
            if wanted not in (base, full) and not full.startswith(wanted):
                continue
            price = _pick_tcgplayer_price(by_product.get(product.get("productId"), []), want_foil)
            if price is None:
                continue
            is_variant = full != base or any(w in name.lower() for w in VARIANT_WORDS)
            score = 0
            if wanted in (base, full):
                score += 3
            if want_num is not None and _card_number(extended["Number"]) == want_num:
                score += 4
            if is_variant == want_variant:
                score += 4
            if named and any(w in name.lower() for w in named):
                score += 3
            candidates.append((score, price, product, group, extended["Number"]))
    if not candidates:
        return None
    score, (value, subtype), product, group, number = max(candidates, key=lambda c: c[0])
    return PriceResult(
        price=value,
        source="TCGplayer (via TCGCSV)",
        matched=f"{product.get('name')} — {group.get('name')} #{number} ({subtype})",
    )


def riftbound_market_prices(fetch=None, report=None) -> dict[str, tuple[str, float]]:
    """Today's price for every Riftbound card, keyed by db.name_key(name).

    Uses each card's cheapest regular (non-showcase) printing across all
    sets, which is the price a player pays to put the card in a deck.
    """
    fetch = fetch or _cached_get_json
    groups = fetch(f"{TCGCSV}/groups").get("results", [])
    if not groups:
        raise PriceLookupError("Couldn't load the Riftbound set list.")
    data = []
    for i, g in enumerate(groups, 1):
        if report:
            report(f"Downloading Riftbound prices… set {i}/{len(groups)}: {g.get('name')}")
        gid = g["groupId"]
        data.append((fetch(f"{TCGCSV}/{gid}/products").get("results", []),
                     fetch(f"{TCGCSV}/{gid}/prices").get("results", [])))
    return parse_riftbound_market(data)


def parse_riftbound_market(data: list[tuple[list[dict], list[dict]]]) -> dict[str, tuple[str, float]]:
    """`data` holds (products, prices) for each set."""
    best: dict[str, tuple[str, float]] = {}
    for products, prices in data:
        by_product: dict[int, list[dict]] = {}
        for price in prices:
            by_product.setdefault(price.get("productId"), []).append(price)
        for product in products:
            extended = {e.get("name") for e in product.get("extendedData") or []}
            name = product.get("name") or product.get("cleanName", "")
            if "Number" not in extended or "(" in name or any(w in name.lower() for w in VARIANT_WORDS):
                continue  # sealed product or a showcase/alt-art printing
            picked = _pick_tcgplayer_price(by_product.get(product.get("productId"), []), want_foil=False)
            if picked is None:
                continue
            key = _norm(name)
            if key not in best or picked[0] < best[key][1]:
                best[key] = (name, picked[0])
    return best


def _pick_tcgplayer_price(prices: list[dict], want_foil: bool) -> tuple[float, str] | None:
    order = ["Foil", "Normal"] if want_foil else ["Normal", "Foil"]
    ranked = sorted(prices, key=lambda p: order.index(p["subTypeName"]) if p.get("subTypeName") in order else 9)
    for p in ranked:
        value = p.get("marketPrice") or p.get("midPrice") or p.get("lowPrice")
        if value:
            return float(value), p.get("subTypeName") or "Normal"
    return None


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
