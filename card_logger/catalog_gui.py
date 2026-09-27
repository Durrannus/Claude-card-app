"""The "All cards" view in the Market tab: every Riftbound printing with its price."""

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import catalog, market, theme
from .charts import LineChart
from .db import Card, CardDatabase, name_key
from .meta import MetaTracker

PRICE_COLOR = "#b8862a"
PLAY_COLOR = "#1f9aaa"
ALL_SETS, ALL_RARITIES = "All sets", "All rarities"
META_FILTERS = ["Any meta", "Rising in meta", "Falling in meta", "Played in meta", "Not played"]
ALL_VERSIONS = "All versions"
# Short labels for the table; the filter and details panel use the full names.
SHORT_VERSION = {"Alternate art": "Alt art", "Overnumbered": "Overnum.", "Special (SP)": "SP"}

# (key, heading, width, anchor, numeric)
COLUMNS = [
    ("name", "Card", 170, "w", False),
    ("number", "#", 104, "w", False),
    ("version", "Version", 116, "w", False),
    ("price", "Price", 88, "e", True),
    ("d1", "1 day", 66, "center", True),
    ("d7", "7 days", 72, "center", True),
    ("cm", "EU price", 100, "e", True),
    ("play", "Play rate", 92, "center", True),
    ("meta", "Meta move", 118, "center", True),
    ("owned", "Own", 56, "center", True),
]


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _pct(v: float | None) -> str:
    return "" if v is None else ("0%" if abs(v) < 0.005 else f"{v:+.0%}")


class AllCardsView(ttk.Frame):
    """Searchable, sortable list of every printing, with details and price history."""

    def __init__(self, parent, db: CardDatabase, meta: MetaTracker, on_data_changed, ebay_site,
                 trend_days=lambda: 7):
        super().__init__(parent)
        self.db, self.meta = db, meta
        self.on_data_changed = on_data_changed
        self.ebay_site = ebay_site
        self.trend_days = trend_days  # meta move compares the last N days of decklists with the N before
        self.cards: list[catalog.CatalogCard] = []
        self.shown: list[catalog.CatalogCard] = []
        self.play: dict[str, float] = {}
        self.trends: dict = {}
        self.owned: dict[str, int] = {}
        self.sort_key, self.sort_reverse = "price", True  # most valuable first

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Search", style="Muted.TLabel").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.fill())
        ttk.Entry(bar, textvariable=self.search, width=24).pack(side="left", padx=(6, 12))
        self.set_var, self.rarity_var = tk.StringVar(value=ALL_SETS), tk.StringVar(value=ALL_RARITIES)
        self.version_var = tk.StringVar(value=ALL_VERSIONS)
        self.set_box = ttk.Combobox(bar, textvariable=self.set_var, state="readonly", width=18)
        self.set_box.pack(side="left")
        self.rarity_box = ttk.Combobox(bar, textvariable=self.rarity_var, state="readonly", width=13)
        self.rarity_box.pack(side="left", padx=(8, 0))
        self.version_box = ttk.Combobox(bar, textvariable=self.version_var, state="readonly", width=14,
                                        values=[ALL_VERSIONS] + catalog.VERSIONS)
        self.version_box.pack(side="left", padx=(8, 0))
        for box in (self.set_box, self.rarity_box, self.version_box):
            box.bind("<<ComboboxSelected>>", lambda _: self.fill())
        self.meta_var = tk.StringVar(value=META_FILTERS[0])
        meta_box = ttk.Combobox(bar, textvariable=self.meta_var, values=META_FILTERS, state="readonly", width=14)
        meta_box.pack(side="left", padx=(8, 0))
        meta_box.bind("<<ComboboxSelected>>", lambda _: self.fill())
        self.min_price = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="$1 and up", variable=self.min_price, command=self.fill).pack(side="left", padx=12)
        self.count = tk.StringVar()
        ttk.Label(bar, textvariable=self.count, style="Muted.TLabel").pack(side="right")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        details = ttk.Frame(body, style="Card.TFrame", padding=14, width=theme.px(336))
        details.pack(side="right", fill="y", padx=(12, 0))
        details.pack_propagate(False)
        self._build_details(details)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        # Charts sit under the list, side by side, where there's width to spare.
        charts = ttk.Frame(left)
        charts.pack(side="bottom", fill="x", pady=(12, 0))
        charts.columnconfigure((0, 1), weight=1, uniform="chart")
        for col, (title, subtitle, attr, color, fmt, zero) in enumerate([
            ("Price history", "this printing, per copy", "price_chart", PRICE_COLOR, _money, False),
            ("Play rate", "% of decklists, weekly", "play_chart", PLAY_COLOR, lambda v: f"{v:.0%}", True),
        ]):
            box = ttk.Frame(charts, style="Card.TFrame", padding=(12, 8))
            box.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
            heading = ttk.Frame(box, style="Header.TFrame")
            heading.pack(anchor="w")
            ttk.Label(heading, text=title, style="CardSection.TLabel").pack(side="left")
            ttk.Label(heading, text="  " + subtitle, style="CardMuted.TLabel").pack(side="left")
            chart = LineChart(box, color, fmt=fmt, zero_based=zero, height=110)
            chart.pack(fill="both", expand=True, pady=(4, 0))
            setattr(self, attr, chart)

        table = ttk.Frame(left, style="Card.TFrame", padding=1)
        table.pack(side="top", fill="both", expand=True)
        # The card number, version and price must never be cut short.
        self.tree = theme.make_table(table, COLUMNS, on_sort=self.sort_by, flexible=("name", "version"),
                                     fit_text={"number": "OGN-303-STAR", "version": "Signature",
                                               "price": "$1,266.68 F"},
                                     selectmode="browse")
        self.tree.tag_configure("up", foreground=theme.GOOD)
        self.tree.tag_configure("down", foreground=theme.BAD)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_details())

    def _build_details(self, panel) -> None:
        self.d_name, self.d_info, self.d_price, self.d_facts = (tk.StringVar() for _ in range(4))
        ttk.Label(panel, textvariable=self.d_name, style="CardName.TLabel", wraplength=theme.px(306)).pack(anchor="w")
        ttk.Label(panel, textvariable=self.d_info, style="CardMuted.TLabel", wraplength=theme.px(306),
                  justify="left").pack(anchor="w", pady=(2, 6))
        ttk.Label(panel, textvariable=self.d_price, style="CardSection.TLabel", font=theme.font(15, "bold")).pack(
            anchor="w")
        ttk.Label(panel, textvariable=self.d_facts, style="Card.TLabel", wraplength=theme.px(306),
                  justify="left").pack(anchor="w", pady=(4, 8))

        buttons = ttk.Frame(panel, style="Header.TFrame")
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        buttons.columnconfigure((0, 1), weight=1, uniform="b")
        self.buttons = [
            ttk.Button(buttons, text="+ Add to collection", style="Small.TButton", command=self.add_to_collection),
            ttk.Button(buttons, text="★ Add to wishlist", style="Small.TButton", command=self.add_to_wishlist),
            ttk.Button(buttons, text="eBay sold ↗", style="Small.TButton", command=self.open_ebay),
            ttk.Button(buttons, text="Card image ↗", style="Small.TButton", command=self.open_image),
        ]
        for i, b in enumerate(self.buttons):
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0 if i % 2 == 0 else 4, 0), pady=(0 if i < 2 else 4, 0))


    # --- data ----------------------------------------------------------------

    def refresh(self) -> None:
        self.cards = catalog.load(self.db)
        self.play = {name_key(u.name): u.share for u in self.meta.card_usage(include_runes=True)}
        self.trends = market.meta_trends(self.meta, self.trend_days())
        self.owned = self.meta.owned_counts()
        sets = sorted({c.set_name for c in self.cards if c.set_name})
        rarities = sorted({c.rarity for c in self.cards if c.rarity})
        self.set_box["values"] = [ALL_SETS] + sets
        self.rarity_box["values"] = [ALL_RARITIES] + rarities
        self.fill()

    def _value(self, c: catalog.CatalogCard, key: str):
        k = name_key(c.base_name)
        return {
            "name": c.base_name.lower(), "number": c.number_key(),
            "version": (catalog.VERSIONS.index(c.version) if c.version in catalog.VERSIONS else 99, c.detail),
            "set": c.set_name.lower(), "rarity": c.rarity.lower(),
            "price": c.main_price or None, "d1": c.change_pct(1), "d7": c.change_pct(7), "cm": c.cm_price or None,
            "play": self.play.get(k, 0.0), "owned": self.owned.get(k, 0),
            "meta": self._meta_points(c),
        }[key]

    def _meta_points(self, c: catalog.CatalogCard) -> float | None:
        trend = self.trends.get(name_key(c.base_name))
        return trend.change_points if trend is not None and trend.enough_data else None

    def _meta_ok(self, c: catalog.CatalogCard) -> bool:
        choice = self.meta_var.get()
        if choice == META_FILTERS[0]:
            return True
        trend = self.trends.get(name_key(c.base_name))
        if choice == "Played in meta":
            return name_key(c.base_name) in self.play
        if choice == "Not played":
            return name_key(c.base_name) not in self.play
        if trend is None or not trend.significant:
            return False
        rising = trend.change_points >= market.RISING_POINTS
        falling = trend.change_points <= market.FALLING_POINTS
        return rising if choice == "Rising in meta" else falling

    def sort_by(self, key: str) -> None:
        numeric = next(c[4] for c in COLUMNS if c[0] == key)
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key, self.sort_reverse = key, numeric  # numbers: biggest first
        self.fill()

    def fill(self) -> None:
        text = self.search.get().strip().lower()
        want_set, want_rarity = self.set_var.get(), self.rarity_var.get()
        shown = [c for c in self.cards
                 if (not text or text in c.name.lower() or text in c.code.lower())
                 and (want_set == ALL_SETS or c.set_name == want_set)
                 and (want_rarity == ALL_RARITIES or c.rarity == want_rarity)
                 and (self.version_var.get() == ALL_VERSIONS or c.version == self.version_var.get())
                 and (not self.min_price.get() or c.main_price >= 1)
                 and self._meta_ok(c)]
        # Cards with no value for the column (e.g. no price) always go last.
        known = [c for c in shown if self._value(c, self.sort_key) is not None]
        unknown = [c for c in shown if self._value(c, self.sort_key) is None]
        known.sort(key=lambda c: self._value(c, self.sort_key), reverse=self.sort_reverse)
        shown = known + unknown
        self.shown = shown
        for key, heading, *_ in COLUMNS:
            arrow = (" ▼" if self.sort_reverse else " ▲") if key == self.sort_key else ""
            self.tree.heading(key, text=heading + arrow)
        self.tree.event_generate("<<Refit>>")

        selected = self.tree.selection()
        keep = self.shown[int(selected[0])].code if selected and int(selected[0]) < len(self.shown) else None
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(shown):
            d7 = c.change_pct(7)
            trend = "up" if d7 and d7 >= 0.05 else ("down" if d7 and d7 <= -0.05 else "")
            k = name_key(c.base_name)
            self.tree.insert("", "end", iid=str(i), tags=(theme.row_tag(i),) + ((trend,) if trend else ()), values=[
                c.base_name, c.code, SHORT_VERSION.get(c.version, c.version),
                (_money(c.main_price) + (" F" if c.is_foil_only else "")) if c.main_price else "—",
                _pct(c.change_pct(1)), _pct(d7),
                f"€{c.cm_price:,.2f}" if c.cm_price else "",
                f"{self.play[k]:.0%}" if k in self.play else "",
                self._meta_text(c),
                self.owned.get(k) or "",
            ])
        total = len(self.cards)
        note = ""
        if self.trends and not any(t.enough_data for t in self.trends.values()):
            note = "Meta move needs more decklists in the earlier period; try another Compare period  ·  "
        self.count.set(note + (f"{len(shown):,} of {total:,} printings" if total else ""))
        if keep:
            again = next((i for i, c in enumerate(shown) if c.code == keep), None)
            if again is not None:
                self.tree.selection_set(str(again))
                self.tree.see(str(again))
        self.show_details()

    def _meta_text(self, c: catalog.CatalogCard) -> str:
        trend = self.trends.get(name_key(c.base_name))
        if trend is None:
            return ""
        if not trend.enough_data:
            return ""  # too few decklists in one of the periods; the count label says so
        if abs(trend.change_points) < 0.5:
            return "0"
        return f"{trend.change_points:+.0f} pts" + ("" if trend.significant else "?")

    def selected(self) -> catalog.CatalogCard | None:
        sel = self.tree.selection()
        return self.shown[int(sel[0])] if sel and int(sel[0]) < len(self.shown) else None

    def show_details(self) -> None:
        c = self.selected()
        for b in self.buttons:
            b.state(["!disabled"] if c else ["disabled"])
        if not c:
            self.d_name.set("Select a card" if self.cards else "No card prices yet")
            self.d_info.set("" if self.cards else "Click \"Update all prices\" to download every Riftbound card "
                                                  "with its current price.")
            self.d_price.set("")
            self.d_facts.set("Tip: click a column heading to sort. Click \"7 days\" for the week's biggest movers "
                             "(tick \"$1 and up\" to skip penny cards)."
                             if self.cards else "")
            self.price_chart.set_data([])
            self.play_chart.set_data([])
            return
        k = name_key(c.base_name)
        self.d_name.set(c.base_name)
        self.d_info.set(" · ".join(filter(None, [c.code, c.version, c.set_name, c.rarity, c.card_type]))
                        + (f"\n{c.detail}" if c.detail else ""))
        self.d_price.set(_money(c.main_price) + ("  foil" if c.is_foil_only else "") if c.main_price else "No price")
        facts = []
        if c.price and c.foil_price:
            facts.append(f"Foil {_money(c.foil_price)}")
        for label, days in (("1 day", 1), ("7 days", 7)):
            if c.change_pct(days) is not None:
                delta = c.change_1d if days == 1 else c.change_7d
                facts.append(f"{label}: no change" if abs(delta) < 0.005 else
                             f"{label}: {_pct(c.change_pct(days))} ({'+' if delta >= 0 else '−'}{_money(abs(delta))})")
        if c.cm_price:
            facts.append(f"EU price (Cardmarket) €{c.cm_price:,.2f}")
        facts.append(f"Played in {self.play[k]:.0%} of decklists" if k in self.play else "Not in your decklists")
        trend = self.trends.get(k)
        if trend is not None and trend.enough_data:
            facts.append(f"Meta move: {trend.previous_share:.0%} → {trend.recent_share:.0%} of decklists "
                         f"({trend.change_points:+.0f} pts, last {self.trend_days()} days vs the {self.trend_days()} before"
                         + (")" if trend.significant else ", could be chance)"))
        if self.owned.get(k):
            facts.append(f"You own {self.owned[k]}")
        self.d_facts.set("\n".join(facts))

        history = catalog.history(self.db, c)
        self.price_chart.set_data(history, "Price history builds up as prices are updated each day. "
                                           "The 7-day change above works already.")
        series = market.play_rate_series(self.meta, c.base_name)
        while series and series[0][1] is None:
            series.pop(0)
        if not any(v >= 0.005 for _, v in series if v is not None):
            series = []  # never (or almost never) played: say so rather than draw a flat line
        self.play_chart.set_data(series, "Not being played in your decklists.")

    # --- actions ---------------------------------------------------------------

    def _card(self, c: catalog.CatalogCard, wishlist: bool) -> Card:
        notes = ", ".join(filter(None, [c.version if c.version != "Standard" else "", c.detail,
                                        "Foil" if c.is_foil_only else ""]))
        return Card(name=c.base_name, game="Riftbound", set_name=c.set_name, number=c.code, rarity=c.rarity,
                    value=c.main_price, wishlist=wishlist, notes=notes)

    def add_to_collection(self) -> None:
        c = self.selected()
        if c:
            from .gui import CardDialog
            dialog = CardDialog(self.winfo_toplevel(), "Add to collection", ["Riftbound"], self._card(c, False))
            if dialog.result:
                self.db.add(dialog.result)
                self.on_data_changed()
                self.refresh()

    def add_to_wishlist(self) -> None:
        c = self.selected()
        if not c:
            return
        if any(w.number == c.code for w in self.db.search(wishlist=True)):
            messagebox.showinfo("Wishlist", f"{c.name} ({c.code}) is already on your wishlist.")
            return
        self.db.add(self._card(c, True))
        self.on_data_changed()
        messagebox.showinfo("Wishlist", f"Added {c.name} ({c.set_name}) to your wishlist.")

    def open_ebay(self) -> None:
        c = self.selected()
        if c:
            words = [c.base_name.replace(" - ", " ")]
            if c.version in ("Alternate art", "Overnumbered", "Signature"):
                words.append(c.version.lower())
            webbrowser.open(market.ebay_sold_url(" ".join(words), "Riftbound", self.ebay_site()))

    def open_image(self) -> None:
        c = self.selected()
        if c and c.image:
            webbrowser.open(c.image)
