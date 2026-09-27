"""Future insight: early signs that a card is about to become popular.

Nothing can know the future meta for certain. What this module does is look
for the patterns that tend to come *before* a card takes off, and score each
card by how many of them it shows:

- Top-cut overperformance: winning players run it more than everyone else
  (players copy winning lists).
- Win rate: decks with it win more games than decks without it.
- Early climb: its play rate has been rising week on week.
- Rising legend: it's a core card of a legend whose meta share is growing.
- Spreading: more legends have started playing it.
- Price moving first: its price is rising faster than its play rate
  (buyers betting on it), including cards nobody plays yet.
- New arrival: it appeared in decklists for the first time recently.

Each sign comes with a plain-language explanation. The score is a way to
rank cards worth watching, not a probability.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from .db import CardDatabase, name_key
from .market import meta_anchor, price_change
from .meta import LEGEND, RUNES, MetaTracker

MIN_DECKS = 20        # decklists needed in the window before judging anything
ALREADY_META = 0.60   # cards in this share of decks are already staples
Z_NEEDED = 1.65       # about 95% sure of the direction of a difference
Z_EARLY = 1.28        # about 90% sure: a lower bar for an early climb, which is the point of this tab
CORE_INCLUSION = 0.60  # "core card" of a legend: in this share of its lists

TOPCUT, WINRATE, CLIMB, LEGEND_PULL, SPREAD, PRICE, NEW, SPECULATION = (
    "Top finishers", "Wins more", "Climbing", "Rising legend", "Spreading", "Price first", "New", "Unplayed, price up",
)


@dataclass
class Sign:
    kind: str
    points: float
    detail: str


@dataclass
class Candidate:
    name: str
    score: float
    signs: list[Sign]
    share: float = 0.0             # share of decks in the window playing it
    recent_share: float = 0.0
    top_share: float | None = None  # share among top finishers
    win_rate: float | None = None   # game win rate of decks with it
    slope: float | None = None      # play-rate change, points per week
    price_move: float | None = None
    owned: int = 0

    @property
    def tags(self) -> str:
        return ", ".join(s.kind for s in sorted(self.signs, key=lambda s: -s.points))


@dataclass
class LegendTrend:
    legend: str
    recent_share: float
    previous_share: float
    decks: int
    z: float

    @property
    def change_points(self) -> float:
        return (self.recent_share - self.previous_share) * 100

    @property
    def rising(self) -> bool:
        return self.change_points >= 5 and self.z >= Z_NEEDED


@dataclass
class Report:
    candidates: list[Candidate] = field(default_factory=list)
    legends: list[LegendTrend] = field(default_factory=list)
    decks: int = 0
    with_placing: int = 0
    with_record: int = 0
    start: str = ""
    end: str = ""
    message: str = ""


def two_prop_z(hits1: float, n1: float, hits2: float, n2: float) -> float:
    """z-score for the difference between two proportions (hits1/n1 - hits2/n2)."""
    if not n1 or not n2:
        return 0.0
    p1, p2 = hits1 / n1, hits2 / n2
    pooled = (hits1 + hits2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se else 0.0


def slope_per_week(points: list[tuple[int, float]]) -> float | None:
    """Least-squares slope of (week index, share) points, in percentage points per week."""
    if len(points) < 3:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    var = sum((x - mx) ** 2 for x, _ in points)
    if not var:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / var * 100


def _is_top(deck) -> bool | None:
    """Top quarter of its event (or top 8 when the event size is unknown)."""
    if not deck.placement:
        return None
    if deck.players:
        return deck.placement <= max(1, round(deck.players * 0.25))
    return deck.placement <= 8


def analyse(db: CardDatabase, meta: MetaTracker, weeks: int = 6, today: date | None = None) -> Report:
    report = Report()
    anchor = meta_anchor(meta)
    if anchor is None:
        report.message = "No decklists yet. Import tournaments in the Meta tracker tab to get insights."
        return _with_speculation(db, meta, report, set(), today)
    start = anchor - timedelta(days=weeks * 7)
    middle = anchor - timedelta(days=weeks * 7 / 2)
    report.start, report.end = start.isoformat(), anchor.isoformat()
    decks = [d for d in meta.decks() if start.isoformat() < d.date <= anchor.isoformat()]
    report.decks = len(decks)
    report.with_placing = sum(1 for d in decks if _is_top(d) is not None)
    report.with_record = sum(1 for d in decks if d.wins is not None and d.losses is not None)
    if len(decks) < MIN_DECKS:
        report.message = (f"Only {len(decks)} decklists in the last {weeks} weeks; at least {MIN_DECKS} are "
                          "needed to spot early signs. Import more tournaments in the Meta tracker tab.")
        return _with_speculation(db, meta, report, set(), today)

    by_id = {d.id: d for d in decks}
    cards_of: dict[int, set[str]] = {d.id: set() for d in decks}
    names: dict[str, str] = {}
    ids = list(by_id)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in meta.conn.execute(
            f"SELECT deck_id, section, name FROM deck_cards WHERE deck_id IN ({','.join('?' * len(chunk))})", chunk
        ):
            if r["section"] in (LEGEND, RUNES):
                continue
            key = name_key(r["name"])
            names.setdefault(key, r["name"])
            cards_of[r["deck_id"]].add(key)

    first_seen: dict[str, str] = {}
    for r in meta.conn.execute(
        "SELECT dc.name AS name, MIN(d.date) AS first FROM deck_cards dc JOIN decks d ON d.id = dc.deck_id "
        "WHERE d.date != '' GROUP BY dc.name"
    ):
        key = name_key(r["name"])
        if key not in first_seen or r["first"] < first_seen[key]:
            first_seen[key] = r["first"]

    recent = {d.id for d in decks if d.date > middle.isoformat()}
    previous = set(by_id) - recent
    week_of = {d.id: min(weeks - 1, (date.fromisoformat(d.date) - start).days // 7) for d in decks}
    week_sizes = [sum(1 for d in decks if week_of[d.id] == w) for w in range(weeks)]
    top = {d.id for d in decks if _is_top(d) is True}
    rest = {d.id for d in decks if _is_top(d) is False}
    recorded = [d for d in decks if d.wins is not None and d.losses is not None]

    # Legend trends, and how often each legend's lists include each card.
    legend_of = {d.id: name_key(d.legend) for d in decks if d.legend}
    legend_names = {name_key(d.legend): d.legend for d in decks if d.legend}
    report.legends = []
    for lkey, lname in legend_names.items():
        in_recent = sum(1 for i in recent if legend_of.get(i) == lkey)
        in_prev = sum(1 for i in previous if legend_of.get(i) == lkey)
        report.legends.append(LegendTrend(
            lname, in_recent / len(recent) if recent else 0.0, in_prev / len(previous) if previous else 0.0,
            in_recent + in_prev, two_prop_z(in_recent, len(recent), in_prev, len(previous)),
        ))
    report.legends.sort(key=lambda t: -t.change_points)
    rising_legends = {name_key(t.legend): t for t in report.legends if t.rising}

    owned = meta.owned_counts()
    history_cutoff = 14
    candidates = []
    for key, name in names.items():
        playing = {i for i, cards in cards_of.items() if key in cards}
        share = len(playing) / len(decks)
        recent_share = len(playing & recent) / len(recent) if recent else 0.0
        prev_share = len(playing & previous) / len(previous) if previous else 0.0
        c = Candidate(name=name, score=0.0, signs=[], share=share, recent_share=recent_share,
                      owned=owned.get(key, 0))
        if share >= ALREADY_META:
            continue  # already a staple: nothing "upcoming" about it

        if len(top) >= 8 and len(rest) >= 8:
            t_hits, r_hits = len(playing & top), len(playing & rest)
            c.top_share = t_hits / len(top)
            diff = c.top_share - r_hits / len(rest)
            z = two_prop_z(t_hits, len(top), r_hits, len(rest))
            if diff >= 0.10 and z >= Z_NEEDED:
                c.signs.append(Sign(TOPCUT, min(30, 10 + diff * 100),
                                    f"Top finishers play it more: in {c.top_share:.0%} of top-quarter decks vs "
                                    f"{r_hits / len(rest):.0%} of the rest. Players tend to copy winning lists."))

        with_games = [(d.wins, d.losses) for d in recorded if d.id in playing]
        without_games = [(d.wins, d.losses) for d in recorded if d.id not in playing]
        w1, g1 = sum(w for w, _ in with_games), sum(w + lo for w, lo in with_games)
        w2, g2 = sum(w for w, _ in without_games), sum(w + lo for w, lo in without_games)
        if g1 >= 40 and g2 >= 40:
            c.win_rate = w1 / g1
            diff = w1 / g1 - w2 / g2
            if diff >= 0.03 and two_prop_z(w1, g1, w2, g2) >= Z_NEEDED:
                c.signs.append(Sign(WINRATE, min(30, 10 + diff * 300),
                                    f"Decks with it win {w1 / g1:.0%} of their games vs {w2 / g2:.0%} without "
                                    f"({g1} and {g2} games). Strong cards get picked up."))

        weekly = [(w, sum(1 for i in playing if week_of[i] == w) / week_sizes[w])
                  for w in range(weeks) if week_sizes[w] >= 3]
        c.slope = slope_per_week(weekly)
        climb_z = two_prop_z(len(playing & recent), len(recent), len(playing & previous), len(previous))
        if (c.slope is not None and c.slope >= 2 and recent_share - prev_share >= 0.05
                and climb_z >= Z_EARLY and recent_share < ALREADY_META):
            c.signs.append(Sign(CLIMB, min(20, c.slope * 3),
                                f"Play rate climbing about {c.slope:+.1f} points a week "
                                f"({prev_share:.0%} → {recent_share:.0%}), still with room to grow."))

        best_pull = None
        for lkey, trend in rising_legends.items():
            legend_decks = [i for i, lk in legend_of.items() if lk == lkey]
            if len(legend_decks) < 5:
                continue
            inclusion = sum(1 for i in legend_decks if i in playing) / len(legend_decks)
            if inclusion >= CORE_INCLUSION and (best_pull is None or trend.change_points > best_pull[1].change_points):
                best_pull = (inclusion, trend)
        if best_pull:
            inclusion, trend = best_pull
            c.signs.append(Sign(LEGEND_PULL, min(20, 8 + trend.change_points),
                                f"Core card of {trend.legend} (in {inclusion:.0%} of its lists), whose meta share "
                                f"is up {trend.change_points:+.0f} pts. The card should rise with it."))

        legends_recent = {legend_of[i] for i in playing & recent if i in legend_of}
        legends_prev = {legend_of[i] for i in playing & previous if i in legend_of}
        if len(legends_recent) - len(legends_prev) >= 2:
            c.signs.append(Sign(SPREAD, min(10, 3 * (len(legends_recent) - len(legends_prev))),
                                f"Now played by {len(legends_recent)} legends, up from {len(legends_prev)}. "
                                "Cards that fit many decks become staples."))

        change = price_change(db.price_history(name=name), history_cutoff)
        if change:
            c.price_move = change.fraction
            if change.fraction >= 0.20 and recent_share - prev_share < 0.05:
                c.signs.append(Sign(PRICE, min(10, change.fraction * 20),
                                    f"Price up {change.fraction:+.0%} in {change.days} days while play hasn't grown "
                                    "yet. Buyers may be ahead of the tournament results."))

        seen = first_seen.get(key, "")
        if seen and seen > (anchor - timedelta(days=14)).isoformat() and len(playing) >= 3:
            c.signs.append(Sign(NEW, 10, f"First appeared in decklists on {seen} and is already in "
                                         f"{len(playing)} lists."))

        c.score = min(100.0, sum(s.points for s in c.signs))
        if c.signs:
            candidates.append(c)

    report.candidates = sorted(candidates, key=lambda c: (-c.score, c.name.lower()))
    return _with_speculation(db, meta, report, set(names), today)


def _with_speculation(db: CardDatabase, meta: MetaTracker, report: Report, played: set[str],
                      today: date | None) -> Report:
    """Add cards nobody plays yet whose price is climbing fast."""
    today = today or date.today()
    since = (today - timedelta(days=45)).isoformat()
    histories: dict[str, list[tuple[str, float]]] = {}
    labels: dict[str, str] = {}
    for r in db.conn.execute(
        "SELECT name_key, name, day, price FROM price_history WHERE card_id IS NULL AND day >= ? ORDER BY day",
        (since,),
    ):
        histories.setdefault(r["name_key"], []).append((r["day"], r["price"]))
        labels[r["name_key"]] = r["name"]
    owned = meta.owned_counts()
    for key, history in histories.items():
        if key in played:
            continue
        change = price_change(history, 14)
        if change and change.fraction >= 0.25 and history[-1][1] >= 0.25:
            sign = Sign(SPECULATION, min(25, change.fraction * 40),
                        f"Not in any decklist yet, but the price is up {change.fraction:+.0%} in {change.days} days. "
                        "The market may be betting on it (a new combo, a spoiler or a streamer) before it shows "
                        "up in tournaments.")
            report.candidates.append(Candidate(name=labels[key], score=sign.points, signs=[sign],
                                               price_move=change.fraction, owned=owned.get(key, 0)))
    report.candidates.sort(key=lambda c: (-c.score, c.name.lower()))
    return report
