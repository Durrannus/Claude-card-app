"""Market analysis: spot cards moving in the meta and suggest when to sell or buy.

Two things are measured for each card:

- Meta trend: the share of decklists playing the card in the most recent
  window (e.g. the last 14 days of decklists) compared with the window
  before it, in percentage points.
- Price change: the latest recorded price compared with the price at the
  start of the same period.

The signals are rules of thumb built from those two numbers. They're a
starting point for research, not a guarantee.
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote_plus

from . import currency
from .db import Card, CardDatabase, name_key
from .meta import LEGEND, RUNES, MetaTracker

RISING_POINTS = 10.0   # play rate up this many points = rising in the meta
FALLING_POINTS = -10.0
PRICE_SPIKE = 0.25     # price up 25%+ = spiked
PRICE_QUIET = 0.10     # price up less than 10% = hasn't reacted yet
MIN_DECKS = 5          # decklists needed in each window to judge a trend
MIN_Z = 1.65           # z-score a change needs (about 95% sure of the direction) to count as a trend
MAX_COPIES = 3         # copies of a card a deck can play

# Signal labels, strongest first.
SELL_HYPE = "SELL – hype peak"
SELL_FALLING = "SELL – leaving meta"
SELL_SPIKE = "SELL – price spike"
HOLD_RISING = "HOLD – rising"
HOLD = "HOLD"
BUY_EARLY = "BUY – early"
WATCH = "WATCH – moving"
NO_SIGNAL = "—"
SIGNAL_ORDER = [SELL_HYPE, SELL_FALLING, SELL_SPIKE, BUY_EARLY, HOLD_RISING, WATCH, HOLD, NO_SIGNAL]


@dataclass
class MetaTrend:
    name: str
    recent_share: float     # 0-1
    previous_share: float   # 0-1
    recent_decks: int       # decklists in the recent window
    previous_decks: int

    @property
    def enough_data(self) -> bool:
        return self.recent_decks >= MIN_DECKS and self.previous_decks >= MIN_DECKS

    @property
    def change_points(self) -> float:
        return (self.recent_share - self.previous_share) * 100

    @property
    def z_score(self) -> float:
        """Two-proportion z-test: how many standard errors the change is.
        With few decklists, a big-looking change can just be chance."""
        n1, n2 = self.recent_decks, self.previous_decks
        if not n1 or not n2:
            return 0.0
        pooled = (self.recent_share * n1 + self.previous_share * n2) / (n1 + n2)
        se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
        return (self.recent_share - self.previous_share) / se if se else 0.0

    @property
    def significant(self) -> bool:
        return self.enough_data and abs(self.z_score) >= MIN_Z


@dataclass
class PriceChange:
    start: float
    end: float
    days: int  # how far back the start price is

    @property
    def fraction(self) -> float:
        return (self.end - self.start) / self.start if self.start else 0.0


@dataclass
class MarketRow:
    name: str
    owned: int
    card: Card | None           # the collection entry, if owned
    value: float                # latest price per copy
    paid: float                 # purchase price per copy (0 = unknown)
    trend: MetaTrend | None
    price_change: PriceChange | None
    signal: str
    reason: str

    @property
    def profit(self) -> float | None:
        if not self.paid or not self.owned:
            return None
        return (self.value - self.paid) * self.owned


# --- meta trends -------------------------------------------------------------


def _windows(anchor: date, days: int) -> tuple[str, str, str]:
    """ISO dates splitting (anchor - 2*days, anchor] into previous and recent halves."""
    return ((anchor - timedelta(days=2 * days)).isoformat(),
            (anchor - timedelta(days=days)).isoformat(),
            anchor.isoformat())


def meta_anchor(meta: MetaTracker) -> date | None:
    """Trends are measured back from the newest decklist, so they stay
    meaningful even if no decklists were added recently."""
    row = meta.conn.execute("SELECT MAX(date) AS d FROM decks WHERE date != ''").fetchone()
    try:
        return date.fromisoformat(row["d"]) if row["d"] else None
    except ValueError:
        return None


def meta_trends(meta: MetaTracker, days: int = 14, top: int | None = None) -> dict[str, MetaTrend]:
    """Play-rate trend for every card seen in either window, keyed by name_key."""
    anchor = meta_anchor(meta)
    if anchor is None:
        return {}
    start, middle, end = _windows(anchor, days)
    decks = [d for d in meta.decks(top=top) if start < d.date <= end]
    recent = {d.id for d in decks if d.date > middle}
    previous = {d.id for d in decks if d.date <= middle}
    if not decks:
        return {}

    names: dict[str, str] = {}
    in_recent: dict[str, set] = {}
    in_previous: dict[str, set] = {}
    ids = [d.id for d in decks]
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start:chunk_start + 500]
        rows = meta.conn.execute(
            f"SELECT deck_id, section, name FROM deck_cards WHERE deck_id IN ({','.join('?' * len(chunk))})", chunk
        )
        for r in rows:
            if r["section"] in (LEGEND, RUNES):
                continue
            key = name_key(r["name"])
            names.setdefault(key, r["name"])
            (in_recent if r["deck_id"] in recent else in_previous).setdefault(key, set()).add(r["deck_id"])

    trends = {}
    for key, name in names.items():
        trends[key] = MetaTrend(
            name=name,
            recent_share=len(in_recent.get(key, ())) / len(recent) if recent else 0.0,
            previous_share=len(in_previous.get(key, ())) / len(previous) if previous else 0.0,
            recent_decks=len(recent),
            previous_decks=len(previous),
        )
    return trends


def play_rate_series(meta: MetaTracker, name: str, bucket_days: int = 7,
                     buckets: int = 12) -> list[tuple[str, float | None]]:
    """Share of decklists playing `name` in each week (oldest first), ending
    at the newest decklist. Weeks with fewer than 3 decklists are None."""
    anchor = meta_anchor(meta)
    if anchor is None:
        return []
    key = name_key(name)
    first = anchor - timedelta(days=bucket_days * buckets)
    decks = [d for d in meta.decks() if first.isoformat() < d.date <= anchor.isoformat()]
    playing = set()
    ids = [d.id for d in decks]
    for chunk_start in range(0, len(ids), 500):
        chunk = ids[chunk_start:chunk_start + 500]
        for r in meta.conn.execute(
            f"SELECT deck_id, name FROM deck_cards WHERE deck_id IN ({','.join('?' * len(chunk))})", chunk
        ):
            if name_key(r["name"]) == key:
                playing.add(r["deck_id"])

    series = []
    for b in range(buckets):
        lo = first + timedelta(days=bucket_days * b)
        hi = lo + timedelta(days=bucket_days)
        week = [d for d in decks if lo.isoformat() < d.date <= hi.isoformat()]
        share = sum(1 for d in week if d.id in playing) / len(week) if len(week) >= 3 else None
        series.append((hi.isoformat(), share))
    return series


# --- prices ------------------------------------------------------------------


def price_change(history: list[tuple[str, float]], days: int) -> PriceChange | None:
    """Latest price against the last price on or before `days` earlier. If
    the history doesn't go back that far, compare with the oldest price."""
    if len(history) < 2:
        return None
    last_day, last_price = history[-1]
    cutoff = (date.fromisoformat(last_day) - timedelta(days=days)).isoformat()
    earlier = [h for h in history[:-1] if h[0] <= cutoff]
    start_day, start_price = earlier[-1] if earlier else history[0]
    span = (date.fromisoformat(last_day) - date.fromisoformat(start_day)).days
    if span <= 0 or start_price <= 0:
        return None
    return PriceChange(start=start_price, end=last_price, days=span)


def recent_sold(sold: list[dict], days: int = 30, today: date | None = None) -> tuple[float, int, str] | None:
    """(average, count, newest day) of logged sold prices from the last `days`
    days; if there are none that recent, the newest one on its own."""
    if not sold:
        return None
    cutoff = ((today or date.today()) - timedelta(days=days)).isoformat()
    recent = [s for s in sold if s["day"] >= cutoff] or sold[:1]
    return sum(s["price"] for s in recent) / len(recent), len(recent), recent[0]["day"]


def ebay_sold_url(name: str, game: str, site: str) -> str:
    """eBay search for completed, sold listings of a card, newest first."""
    query = name if (game and "riftbound" not in game.lower()) else f"{name} riftbound"
    return f"https://www.{site}/sch/i.html?_nkw={quote_plus(query)}&LH_Sold=1&LH_Complete=1&_sop=13"


# --- signals -----------------------------------------------------------------


def _pct(fraction: float) -> str:
    return f"{fraction:+.0%}"


def decide(owned: int, trend: MetaTrend | None, change: PriceChange | None) -> tuple[str, str]:
    """Return (signal, explanation) for one card."""
    known = trend is not None and trend.enough_data
    pts = trend.change_points if known else 0.0
    significant = known and trend.significant
    rising = significant and pts >= RISING_POINTS
    falling = significant and pts <= FALLING_POINTS
    noise = known and not significant and abs(pts) >= RISING_POINTS
    price = change.fraction if change else None
    spiked = price is not None and price >= PRICE_SPIKE
    quiet = price is None or price < PRICE_QUIET
    price_txt = f"price {_pct(price)} over {change.days} days" if change else "no price history yet"
    play_txt = (f"play rate {trend.previous_share:.0%} → {trend.recent_share:.0%} ({pts:+.0f} pts)"
                if known else "")

    if owned:
        if rising and spiked:
            return SELL_HYPE, (f"Both demand and price are up: {play_txt}, {price_txt}. Hype peaks tend "
                               "to be good moments to sell spare copies before prices settle.")
        if rising:
            return HOLD_RISING, (f"Being played more: {play_txt}, but {price_txt}. The price may not have "
                                 "caught up yet, so holding could pay off.")
        if falling:
            return SELL_FALLING, (f"Dropping out of the meta: {play_txt}. Prices usually follow play rate "
                                  f"down ({price_txt}), so selling sooner may get a better price.")
        if spiked:
            return SELL_SPIKE, (f"Price jumped ({price_txt}) without more play"
                                + (f" ({play_txt})" if known else "")
                                + ". Spikes without demand often fall back.")
        if noise:
            return HOLD, (f"Play rate moved ({play_txt}), but with {trend.recent_decks} and "
                          f"{trend.previous_decks} decklists that's within normal variation. {price_txt.capitalize()}.")
        if trend is not None and not known:
            return HOLD, f"Played in the meta, but not enough decklists yet to see a trend. {price_txt.capitalize()}."
        if known:
            return HOLD, f"Steady: {play_txt}, {price_txt}."
        return NO_SIGNAL, f"Not seen in your decklists. {price_txt.capitalize()}."

    if rising and quiet:
        return BUY_EARLY, (f"Being played more ({play_txt}) but {price_txt}. Buying before the price "
                           "reacts could pay off.")
    if rising:
        return WATCH, f"Being played more ({play_txt}) and the price is already moving ({price_txt})."
    if noise:
        return NO_SIGNAL, (f"{play_txt.capitalize()}, but that's within normal variation for this many "
                           "decklists. Add more to confirm.")
    return NO_SIGNAL, (f"{play_txt.capitalize()}, {price_txt}." if known else "Not enough decklists to see a trend.")


def analyse(db: CardDatabase, meta: MetaTracker, days: int = 14, top: int | None = None) -> list[MarketRow]:
    """One row per card you own, plus meta cards you don't own, strongest signal first."""
    trends = meta_trends(meta, days, top)
    rows: list[MarketRow] = []

    owned: dict[str, list[Card]] = {}
    for card in db.search():
        owned.setdefault(name_key(card.name), []).append(card)

    for key, cards in owned.items():
        card = max(cards, key=lambda c: c.value)
        qty = sum(c.quantity for c in cards)
        paid_total = sum(c.purchase_price * c.quantity for c in cards if c.purchase_price)
        paid_qty = sum(c.quantity for c in cards if c.purchase_price)
        history = db.price_history(card_id=card.id)
        if len(history) < 2:
            history = db.price_history(name=card.name) or history
        change = price_change(history, days)
        is_meta_game = not card.game or "riftbound" in name_key(card.game)
        trend = trends.get(key) if is_meta_game else None
        signal, reason = decide(qty, trend, change)
        # Prices paid are typed in your currency; rows work in US dollars like the prices.
        paid = currency.to_usd(paid_total / paid_qty) if paid_qty else 0.0
        rows.append(MarketRow(card.name, qty, card, card.value, paid,
                              trend, change, signal, reason))

    for key, trend in trends.items():
        if key in owned:
            continue
        history = db.price_history(name=trend.name)
        change = price_change(history, days)
        signal, reason = decide(0, trend, change)
        rows.append(MarketRow(trend.name, 0, None, history[-1][1] if history else 0.0, 0.0,
                              trend, change, signal, reason))

    def sort_key(r: MarketRow):
        momentum = -abs(r.trend.change_points) if r.trend and r.trend.significant else 0
        return SIGNAL_ORDER.index(r.signal), momentum, r.name.lower()

    rows.sort(key=sort_key)
    return rows
