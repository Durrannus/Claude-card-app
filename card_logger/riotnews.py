"""Official decklists from Riot's "Top Decks" articles on playriftbound.com.

After each Regional Qualifier, Riot publishes an article (e.g. "Los Angeles'
Top Decks") with the Top 8 decklists and the best-placed deck of every other
legend ("Best-of decks"), each with the player's overall ranking. These are
the only published decklists for most Regional Qualifiers: the official
tournament system keeps them private, and riftbound.gg and TopDeck.gg don't
carry them.

The articles are found on the news page and read from the page itself (the
site allows it: its robots.txt allows everything). Note the decks are a
selection, one per legend plus the Top 8, so the legends' shares among them
don't reflect the whole field; the article's own legend breakdown is used
for the event's player count.
"""

import html
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .meta import Deck, MetaTracker, card_key, parse_decklist

SITE = "https://playriftbound.com"
NEWS = SITE + "/en-us/news/"
SOURCE = "riot"
HEADERS = {"User-Agent": "Mozilla/5.0 (CardCollectionLogger/1.0)", "Accept": "text/html"}
PAUSE = 1.0
ARTICLE_LINK = re.compile(r"/en-us/news/(?:[a-z0-9-]+/)?(?:[a-z0-9-]*top-decks|the-best-decks-out-of-[a-z0-9-]+)/?")


class FetchError(Exception):
    """The articles couldn't be read; the message is suitable for the user."""


def _get(url: str, fetch=None) -> str:
    if fetch:
        return fetch(url)
    request = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise FetchError(f"playriftbound.com returned an error ({e.code}). Try again later.") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise FetchError("Could not reach playriftbound.com. Check your internet connection.") from e
    raise FetchError("playriftbound.com is busy. Try again later.")


def article_links(page: str) -> list[str]:
    """Full URLs of the "Top Decks" articles linked from a news page, newest first as listed."""
    seen = []
    for path in ARTICLE_LINK.findall(page):
        url = SITE + path.rstrip("/") + "/"
        if url not in seen:
            seen.append(url)
    return seen


@dataclass
class Article:
    url: str
    title: str
    event: str
    published: str     # ISO date the article came out
    day: str           # the event's (final) day: the Sunday before publication
    players: int | None
    decks: list[Deck] = field(default_factory=list)


def _next_data(page: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        raise FetchError("The article's layout has changed; its decklists couldn't be read.")
    return json.loads(m.group(1))


def _event_name(title: str) -> str:
    """ "Los Angeles’ Top Decks" -> "Regional Qualifier Los Angeles"."""
    t = html.unescape(title).replace("’", "'")
    t = re.sub(r"^.*?\bRQ\s+", "", t)
    t = re.sub(r"^the best decks out of\s+", "", t, flags=re.I)
    t = re.sub(r"'s?\s+top decks.*$", "", t, flags=re.I)
    t = re.sub(r"\s+top decks.*$", "", t, flags=re.I)
    return f"Regional Qualifier {t.strip()}"


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>|</p>|</h\d>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return re.sub(r"[ \t]+:", ":", text)  # older articles: "<strong>Legend</strong>:"


def _players(body: str) -> int | None:
    """Day 1 field size, from the article's legend breakdown table."""
    for table in re.findall(r"<table>(.*?)</table>", body, re.S):
        if "% of Field" not in table:
            continue
        total = 0
        for row in re.findall(r"<tr>(.*?)</tr>", table, re.S):
            cells = [_text(c).strip() for c in re.findall(r"<td>(.*?)</td>", row, re.S)]
            if len(cells) >= 2 and cells[1].isdigit():
                total += int(cells[1])
        return total or None
    return None


def parse_article(page: str, url: str) -> Article:
    data = _next_data(page)["props"]["pageProps"]["page"]
    title = data.get("title") or ""
    published = (data.get("displayedPublishDate") or data.get("analytics", {}).get("publishDate") or "")[:10]
    try:
        pub = date.fromisoformat(published)
        day = (pub - timedelta(days=(pub.weekday() + 1) % 7 or 7)).isoformat()  # the Sunday before
    except ValueError:
        day = ""
    body = "".join(b.get("richText", {}).get("body", "") for b in data.get("blades", [])
                   if b.get("type") == "articleRichText")
    article = Article(url, title, _event_name(title), published, day, _players(body))
    seen = set()
    for table in re.findall(r"<table>(.*?)</table>", body, re.S):
        text = _text(table)
        if "Legend:" not in text:
            continue
        player = re.search(r"<h3>(.*?)</h3>", table, re.S)
        player = _text(player.group(1)).strip() if player else ""
        rank = re.search(r"Overall Ranking:\s*#?(\d+)", text)
        # From the first section heading on, the table reads like a text decklist.
        start = text.find("Legend:")
        deck = parse_decklist(text[start:])
        if not deck.cards or not deck.legend:
            continue
        key = (card_key(player), deck.legend)
        if key in seen:  # Top 8 players' decks are shown twice
            continue
        seen.add(key)
        deck.player = player
        deck.placement = int(rank.group(1)) if rank else None
        deck.event, deck.date, deck.players = article.event, day, article.players
        deck.notes = f"Official decklist from Riot: {url}"
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        deck.source_id = f"{SOURCE}:{slug}:{card_key(player)}:{card_key(deck.legend)}"
        article.decks.append(deck)
    return article


@dataclass
class FetchResult:
    decks: list[Deck] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)


def fetch(days: int, known_sources: set[str], fetch=None, report=None, today: date | None = None,
          pause: float = PAUSE) -> FetchResult:
    """Decklists from "Top Decks" articles published in the last `days` days."""
    today = today or datetime.now().date()
    since = (today - timedelta(days=days)).isoformat()
    if report:
        report("Reading Riot's news for Regional Qualifier decklists…")
    result = FetchResult()
    for url in article_links(_get(NEWS, fetch)):
        time.sleep(pause)
        article = parse_article(_get(url, fetch), url)
        if article.published and article.published < since:
            continue
        if report:
            report(f"Reading {article.title}…")
        result.articles.append(article)
        result.decks += [d for d in article.decks if d.source_id not in known_sources]
    return result


def save(meta: MetaTracker, result: FetchResult) -> int:
    added = 0
    for deck in result.decks:
        try:
            meta.add_deck(deck)
            added += 1
        except ValueError:
            continue
    return added
