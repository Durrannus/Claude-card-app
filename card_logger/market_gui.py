"""The "Market" tab: sell/hold/buy signals from price history and meta trends."""

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

from . import market, pricing, theme
from .charts import LineChart
from .db import CardDatabase
from .meta import MetaTracker

PRICE_COLOR = "#b8862a"  # validated series colours for the dark chart surface
PLAY_COLOR = "#1f9aaa"

MY_CARDS, OPPORTUNITIES, EVERYTHING = "mine", "opportunities", "all"
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
TAG_COLORS = {"sell": theme.GOLD, "buy": theme.GOOD, "rising": "#4fd1c5", "watch": "#9fc3e0",
              "hold": theme.TEXT, "none": theme.MUTED}

COLUMNS = [
    ("signal", "Signal", 145, "w"),
    ("name", "Card", 160, "w"),
    ("owned", "Own", 50, "center"),
    ("value", "Value", 70, "e"),
    ("paid", "Paid", 65, "e"),
    ("profit", "Profit", 75, "e"),
    ("price", "Price move", 108, "center"),
    ("play", "Play rate", 92, "center"),
    ("meta", "Meta move", 102, "center"),
]


def _money(v: float) -> str:
    return f"${v:,.2f}"


class MarketTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, db: CardDatabase, on_data_changed=None):
        super().__init__(parent, padding=(16, 14, 16, 0))
        self.db = db
        self.meta = MetaTracker(db)
        self.on_data_changed = on_data_changed or (lambda: None)
        self.rows: list[market.MarketRow] = []
        self.busy = False

        self._build_tiles()
        self._build_controls()
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", pady=(10, 0)
        )
        self._build_body()
        self.refresh()

        if self.auto_var.get() and db.get_setting("last_auto_update") != date.today().isoformat():
            self.after(1500, lambda: self.update_all_prices(silent=True))

    # --- layout ------------------------------------------------------------

    def _build_tiles(self) -> None:
        tiles = ttk.Frame(self)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {
            "profit": theme.StatTile(tiles, "Profit on cards with a purchase price", gold=True),
            "sell": theme.StatTile(tiles, "Sell signals"),
            "buy": theme.StatTile(tiles, "Buy signals"),
            "updated": theme.StatTile(tiles, "Prices last updated"),
        }
        for i, tile in enumerate(self.tiles.values()):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

    def _build_controls(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))
        self.view_var = tk.StringVar(value=MY_CARDS)
        for text, value in [("My cards", MY_CARDS), ("Buy opportunities", OPPORTUNITIES), ("Everything", EVERYTHING)]:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.view_var, style="Segment.TRadiobutton",
                            command=self.refresh).pack(side="left")

        ttk.Label(bar, text="Compare", style="Muted.TLabel").pack(side="left", padx=(20, 6))
        self.window_var = tk.StringVar(value="Last 14 days")
        box = ttk.Combobox(bar, textvariable=self.window_var, values=list(WINDOWS), state="readonly", width=13)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        self.signal_var = tk.StringVar(value="All signals")
        box = ttk.Combobox(bar, textvariable=self.signal_var, values=list(SIGNAL_FILTERS), state="readonly", width=12)
        box.pack(side="left", padx=(10, 0))
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        self.update_button = ttk.Button(bar, text="↻  Update all prices", style="Accent.TButton",
                                        command=self.update_all_prices)
        self.update_button.pack(side="right")
        self.auto_var = tk.BooleanVar(value=self.db.get_setting("auto_update", "1") == "1")
        ttk.Checkbutton(bar, text="Update daily when the app opens", variable=self.auto_var,
                        command=lambda: self.db.set_setting("auto_update", "1" if self.auto_var.get() else "0")
                        ).pack(side="right", padx=(0, 12))

    def _build_body(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        details = ttk.Frame(body, style="Card.TFrame", padding=14, width=350)
        details.pack(side="right", fill="y", padx=(12, 0))
        details.pack_propagate(False)
        self._build_details(details)

        table = ttk.Frame(body, style="Card.TFrame", padding=1)
        table.pack(side="left", fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended")
        for key, heading, width, anchor in COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor=anchor, minwidth=40)
        theme.stripe(self.tree)
        for tag, color in TAG_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_details())

    def _build_details(self, panel: ttk.Frame) -> None:
        self.d_name = tk.StringVar()
        self.d_signal = tk.StringVar()
        self.d_reason = tk.StringVar()
        self.d_facts = tk.StringVar()
        ttk.Label(panel, textvariable=self.d_name, style="CardName.TLabel", wraplength=320).pack(anchor="w")
        self.signal_label = tk.Label(panel, textvariable=self.d_signal, background=theme.SURFACE,
                                     font=theme.font(13, "bold"), anchor="w")
        self.signal_label.pack(anchor="w", pady=(4, 2))
        ttk.Label(panel, textvariable=self.d_reason, style="Card.TLabel", wraplength=320,
                  justify="left").pack(anchor="w")
        ttk.Label(panel, textvariable=self.d_facts, style="CardMuted.TLabel", wraplength=320,
                  justify="left").pack(anchor="w", pady=(6, 8))

        # The two charts split the remaining height evenly (grid shrinks rows
        # by weight), so both stay visible on laptop screens.
        charts = ttk.Frame(panel, style="Header.TFrame")
        charts.pack(fill="both", expand=True)
        charts.columnconfigure(0, weight=1)
        ttk.Label(charts, text="Price", style="CardSection.TLabel").grid(row=0, column=0, sticky="w")
        self.price_chart = LineChart(charts, PRICE_COLOR, fmt=_money, height=60)
        self.price_chart.grid(row=1, column=0, sticky="nsew", pady=(2, 6))
        heading = ttk.Frame(charts, style="Header.TFrame")
        heading.grid(row=2, column=0, sticky="w")
        ttk.Label(heading, text="Play rate", style="CardSection.TLabel").pack(side="left")
        ttk.Label(heading, text="  % of decklists, weekly", style="CardMuted.TLabel").pack(side="left")
        self.play_chart = LineChart(charts, PLAY_COLOR, fmt=lambda v: f"{v:.0%}", zero_based=True, height=60)
        self.play_chart.grid(row=3, column=0, sticky="nsew", pady=(2, 0))
        charts.rowconfigure(1, weight=1, uniform="chart")
        charts.rowconfigure(3, weight=1, uniform="chart")

    # --- display -------------------------------------------------------------

    def refresh(self) -> None:
        days = WINDOWS[self.window_var.get()]
        self.rows = market.analyse(self.db, self.meta, days=days)
        view = self.view_var.get()
        wanted = SIGNAL_FILTERS[self.signal_var.get()]
        shown = [(idx, r) for idx, r in enumerate(self.rows)
                 if (view == EVERYTHING or (view == MY_CARDS) == (r.owned > 0))
                 and (wanted is None or r.signal in wanted)]

        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for i, (idx, r) in enumerate(shown):
            trend_known = r.trend is not None and r.trend.enough_data
            profit = r.profit
            self.tree.insert("", "end", iid=str(idx),
                             tags=(SIGNAL_TAGS[r.signal], theme.row_tag(i)), values=[
                r.signal,
                r.name,
                r.owned or "",
                _money(r.value) if r.value else "",
                _money(r.paid) if r.paid else "",
                (("+" if profit >= 0 else "−") + _money(abs(profit))) if profit is not None else "",
                f"{r.price_change.fraction:+.0%} ({r.price_change.days}d)" if r.price_change else "",
                f"{r.trend.recent_share:.0%}" if r.trend else "",
                (f"{r.trend.change_points:+.0f} pts" + ("" if r.trend.significant else "?"))
                if trend_known else ("few decks" if r.trend else ""),
            ])
        self.tree.selection_set([i for i in selected if self.tree.exists(i)])

        profits = [r.profit for r in self.rows if r.profit is not None]
        self.tiles["profit"].value.set(
            ("+" if sum(profits) >= 0 else "−") + _money(abs(sum(profits))) if profits else "Add prices paid"
        )
        self.tiles["sell"].value.set(str(sum(1 for r in self.rows if SIGNAL_TAGS[r.signal] == "sell")))
        self.tiles["buy"].value.set(str(sum(1 for r in self.rows if r.signal == market.BUY_EARLY)))
        last = self.db.last_price_update()
        self.tiles["updated"].value.set(_friendly_day(last) if last else "Never")

        anchor = market.meta_anchor(self.meta)
        if not self.busy:
            self.status.set(
                f"{len(shown)} cards · meta move: last {days} days of decklists"
                f"{f' (to {anchor:%d %b})' if anchor else ''} vs the {days} before · "
                "\"?\" = could be chance · signals are rules of thumb, not guarantees"
            )
        self.show_details()

    def show_details(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self.d_name.set("Select a card")
            self.d_signal.set("")
            self.d_reason.set("Pick a row to see why it got its signal, with its price and play-rate history.")
            self.d_facts.set("")
            self.price_chart.set_data([])
            self.play_chart.set_data([])
            return
        r = self.rows[int(sel[0])]
        self.d_name.set(r.name)
        self.d_signal.set(r.signal if r.signal != market.NO_SIGNAL else "No signal")
        self.signal_label.configure(foreground=TAG_COLORS[SIGNAL_TAGS[r.signal]])
        self.d_reason.set(r.reason)
        facts = []
        if r.owned:
            facts.append(f"You own {r.owned}")
            if r.owned > market.MAX_COPIES:
                facts.append(f"{r.owned - market.MAX_COPIES} spare beyond a playset")
        if r.value:
            facts.append(f"worth {_money(r.value)} each")
        if r.paid:
            facts.append(f"paid {_money(r.paid)}")
        self.d_facts.set(" · ".join(facts))

        history = self.db.price_history(card_id=r.card.id) if r.card else []
        if len(history) < 2:
            history = self.db.price_history(name=r.name) or history
        self.price_chart.set_data(history, "Not enough price history yet. Prices are saved each time "
                                           "they're updated, so this fills in over time.")
        series = market.play_rate_series(self.meta, r.name)
        while series and series[0][1] is None:  # skip weeks before any decklists
            series.pop(0)
        self.play_chart.set_data(series,
                                 "Not enough dated decklists yet to chart play rate.")

    # --- actions -------------------------------------------------------------

    def update_all_prices(self, silent: bool = False) -> None:
        """Refresh every collection and wishlist price, and save today's
        Riftbound market prices so meta cards build a price history too."""
        from .gui import run_in_background

        if self.busy:
            return
        cards = [c for c in self.db.search(wishlist=None) if pricing.supported(c)]

        def work(report):
            snapshot, snapshot_error = {}, None
            try:
                snapshot = pricing.riftbound_market_prices(report=report)
            except pricing.PriceLookupError as e:
                snapshot_error = str(e)
            found, failed = [], []
            for i, card in enumerate(cards, 1):
                report(f"Updating prices… {i}/{len(cards)}: {card.name}")
                try:
                    found.append((card.id, pricing.lookup_price(card).price))
                except pricing.PriceLookupError as e:
                    failed.append((card.name, str(e)))
            return snapshot, snapshot_error, found, failed

        def done(result, error):
            self.busy = False
            self.update_button.state(["!disabled"])
            if error:
                self.refresh()
                if not silent:
                    messagebox.showerror("Update prices", f"Price update stopped: {error}")
                return
            snapshot, snapshot_error, found, failed = result
            for name, price in snapshot.values():
                self.db.record_price(name, price, game="Riftbound", commit=False)
            self.db.conn.commit()
            for card_id, price in found:
                card = self.db.get(card_id)
                if card:
                    card.value = price
                    self.db.update(card)
            self.db.set_setting("last_auto_update", date.today().isoformat())
            self.on_data_changed()
            self.refresh()
            if silent:
                self.status.set(f"Prices updated automatically: {len(found)} of your cards, "
                                f"{len(snapshot)} Riftbound market prices.")
                return
            lines = [f"Updated {len(found)} of {len(cards)} cards in your collection and wishlist."]
            lines.append(f"Saved today's price for {len(snapshot)} Riftbound cards." if snapshot
                         else f"Riftbound market prices not saved: {snapshot_error}")
            if failed:
                lines.append(f"\nCouldn't price {len(failed)}:")
                lines += [f"  • {n}: {msg}" for n, msg in failed[:8]]
                if len(failed) > 8:
                    lines.append(f"  …and {len(failed) - 8} more")
            messagebox.showinfo("Update prices", "\n".join(lines))

        self.busy = True
        self.update_button.state(["disabled"])
        self.status.set("Updating prices…")
        run_in_background(self, work, done, on_progress=self.status.set)


def _friendly_day(iso: str) -> str:
    days = (date.today() - date.fromisoformat(iso)).days
    return {0: "Today", 1: "Yesterday"}.get(days, f"{days} days ago")
