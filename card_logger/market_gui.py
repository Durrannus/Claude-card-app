"""The "Market" tab: sell/hold/buy signals from price history and meta trends."""

import tkinter as tk
import webbrowser
from datetime import date
from tkinter import messagebox, ttk

from . import catalog, currency, market, pricing, riftboundgg, theme
from .catalog_gui import AllCardsView
from .charts import LineChart
from .db import CardDatabase
from .meta import MetaTracker

PRICE_COLOR = "#b8862a"  # validated series colours for the dark chart surface
PLAY_COLOR = "#1f9aaa"

EBAY_SITES = ["ebay.com", "ebay.co.uk", "ebay.com.au", "ebay.ca", "ebay.de", "ebay.fr", "ebay.it", "ebay.es"]

ALL_CARDS, MY_CARDS, OPPORTUNITIES, EVERYTHING = "catalog", "mine", "opportunities", "all"
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
    ("signal", "Signal", 140, "w"),
    ("name", "Card", 140, "w"),
    ("owned", "Own", 58, "center"),
    ("value", "Value", 70, "e"),
    ("ebay", "eBay sold", 96, "e"),
    ("profit", "Profit", 74, "e"),
    ("price", "Price move", 116, "center"),
    ("play", "Play rate", 96, "center"),
    ("meta", "Meta move", 116, "center"),
]


def _money(usd: float) -> str:
    return currency.fmt(usd)


class SoldPriceDialog(tk.Toplevel):
    """Log sold prices you've seen (e.g. on eBay) for one card, or remove them."""

    def __init__(self, parent, db: CardDatabase, name: str):
        super().__init__(parent)
        self.title("Sold prices")
        self.transient(parent)
        self.resizable(False, False)
        self.db, self.name = db, name
        self.changed = False

        form = ttk.Frame(self, padding=20)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text=name, style="Section.TLabel", font=theme.font(14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(form, style="Muted.TLabel", wraplength=theme.px(380), justify="left", text=(
            "Add each sold price you see, one at a time: the price one copy sold for, in "
            f"{currency.label()} (the currency chosen at the top of the window).")).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 12))

        self.price = tk.StringVar()
        self.day = tk.StringVar(value=date.today().isoformat())
        self.note = tk.StringVar()
        for row, (label, var, width, hint) in enumerate([
            ("Sold for", self.price, 12, "e.g. 4.50"),
            ("Date sold", self.day, 12, "YYYY-MM-DD"),
            ("Note", self.note, 30, "optional, e.g. near mint, foil"),
        ], start=2):
            ttk.Label(form, text=label, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
            entry = ttk.Entry(form, textvariable=var, width=width)
            entry.grid(row=row, column=1, sticky="w")
            ttk.Label(form, text=hint, style="Muted.TLabel", font=theme.font(9)).grid(row=row, column=2, sticky="w",
                                                                                       padx=(10, 0))
            if row == 2:
                entry.focus_set()
        ttk.Button(form, text="Add price", style="Accent.TButton", command=self._add).grid(
            row=5, column=1, sticky="w", pady=(8, 14))

        ttk.Label(form, text="Logged so far", style="Section.TLabel").grid(row=6, column=0, columnspan=3, sticky="w")
        self.listbox = tk.Listbox(form, height=7, width=52, background=theme.RAISED, foreground=theme.TEXT,
                                  selectbackground=theme.SELECT, highlightthickness=0, relief="flat",
                                  font=theme.font(10), activestyle="none")
        self.listbox.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(4, 6))
        buttons = ttk.Frame(form)
        buttons.grid(row=8, column=0, columnspan=3, sticky="ew")
        ttk.Button(buttons, text="Remove selected", style="Danger.TButton", command=self._remove).pack(side="left")
        ttk.Button(buttons, text="Done", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda e: self._add())
        self.bind("<Escape>", lambda e: self.destroy())
        self._load()
        self.grab_set()
        self.wait_window()

    def _load(self) -> None:
        self._rows = self.db.sold_prices(self.name)
        self.listbox.delete(0, "end")
        for r in self._rows:
            self.listbox.insert("end", f"{r['day']}    {currency.fmt_local(r['price'])}    {r['note']}")
        if not self._rows:
            self.listbox.insert("end", "Nothing logged yet.")

    def _add(self) -> None:
        try:
            price = currency.parse(self.price.get())
            day = date.fromisoformat(self.day.get().strip()).isoformat()
            self.db.add_sold_price(self.name, price, day, self.note.get())
        except ValueError:
            messagebox.showerror("Sold price", "Enter a price above zero and a date like 2026-09-27.", parent=self)
            return
        self.changed = True
        self.price.set("")
        self.note.set("")
        self._load()

    def _remove(self) -> None:
        sel = self.listbox.curselection()
        if sel and self._rows:
            self.db.delete_sold_price(self._rows[sel[0]]["id"])
            self.changed = True
            self._load()


class MarketTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, db: CardDatabase, on_data_changed=None):
        super().__init__(parent, padding=(16, 14, 16, 0))
        self.db = db
        self.meta = MetaTracker(db)
        self.on_data_changed = on_data_changed or (lambda: None)
        self.rows: list[market.MarketRow] = []
        self.busy = False
        self.sort_key, self.sort_reverse = None, True  # None: strongest signal first

        self._build_tiles()
        self._build_controls()
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", pady=(10, 0)
        )
        self._build_body()
        self._switch_view()

        # Fetch prices on first run (so every card shows straight away) and
        # then once a day if automatic updates are on.
        first_run = not db.get_setting("catalog_updated")
        if first_run or (self.auto_var.get() and db.get_setting("last_auto_update") != date.today().isoformat()):
            self.after(1500, lambda: self.update_all_prices(silent=True))

    # --- layout ------------------------------------------------------------

    def _build_tiles(self) -> None:
        tiles = self.tiles_frame = ttk.Frame(self)
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
        bar = self.controls_bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))
        self.view_var = tk.StringVar(value=ALL_CARDS)
        for text, value in [("All cards", ALL_CARDS), ("My cards", MY_CARDS),
                            ("Buy opportunities", OPPORTUNITIES), ("Everything", EVERYTHING)]:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.view_var, style="Segment.TRadiobutton",
                            command=self._switch_view).pack(side="left")

        # The comparison period drives "meta move" and price moves in every view.
        ttk.Label(bar, text="Compare", style="Muted.TLabel").pack(side="left", padx=(20, 6))
        self.window_var = tk.StringVar(value="Last 7 days")
        box = ttk.Combobox(bar, textvariable=self.window_var, values=list(WINDOWS), state="readonly", width=13)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        # Only the signal views use this.
        self.signal_controls = ttk.Frame(bar)

        self.signal_var = tk.StringVar(value="All signals")
        box = ttk.Combobox(self.signal_controls, textvariable=self.signal_var, values=list(SIGNAL_FILTERS),
                           state="readonly", width=12)
        box.pack(side="left", padx=(10, 0))
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        self.update_button = ttk.Button(bar, text="↻  Update all prices", style="Accent.TButton",
                                        command=self.update_all_prices)
        self.update_button.pack(side="right")
        self.auto_var = tk.BooleanVar(value=self.db.get_setting("auto_update", "1") == "1")
        ttk.Checkbutton(bar, text="Auto-update daily", variable=self.auto_var,
                        command=lambda: self.db.set_setting("auto_update", "1" if self.auto_var.get() else "0")
                        ).pack(side="right", padx=(0, 12))

    def _build_body(self) -> None:
        # Two bodies share the space: the all-cards price list and the signal views.
        self.all_cards = AllCardsView(self, self.db, self.meta, self.on_data_changed, lambda: self.site_var.get(),
                                      trend_days=lambda: WINDOWS[self.window_var.get()])
        body = self.signal_body = ttk.Frame(self)

        details = ttk.Frame(body, style="Card.TFrame", padding=14, width=theme.px(336))
        details.pack(side="right", fill="y", padx=(12, 0))
        details.pack_propagate(False)
        self._build_details(details)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        # Charts sit under the table, side by side, where there's width to spare.
        charts = ttk.Frame(left)
        charts.pack(side="bottom", fill="x", pady=(12, 0))
        charts.columnconfigure((0, 1), weight=1, uniform="chart")
        for col, (title, subtitle, attr, color, fmt, zero) in enumerate([
            ("Price", "per copy", "price_chart", PRICE_COLOR, _money, False),
            ("Play rate", "% of decklists, weekly", "play_chart", PLAY_COLOR, lambda v: f"{v:.0%}", True),
        ]):
            card = ttk.Frame(charts, style="Card.TFrame", padding=(12, 8))
            card.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 12, 0))
            heading = ttk.Frame(card, style="Header.TFrame")
            heading.pack(anchor="w")
            ttk.Label(heading, text=title, style="CardSection.TLabel").pack(side="left")
            ttk.Label(heading, text="  " + subtitle, style="CardMuted.TLabel").pack(side="left")
            chart = LineChart(card, color, fmt=fmt, zero_based=zero, height=120)
            chart.pack(fill="both", expand=True, pady=(4, 0))
            setattr(self, attr, chart)

        table = ttk.Frame(left, style="Card.TFrame", padding=1)
        table.pack(side="top", fill="both", expand=True)
        self.tree = theme.make_table(table, COLUMNS, on_sort=self.sort_by, flexible=("name", "signal"),
                                     selectmode="extended")
        for tag, color in TAG_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_details())

    def _build_details(self, panel: ttk.Frame) -> None:
        self.d_name = tk.StringVar()
        self.d_signal = tk.StringVar()
        self.d_reason = tk.StringVar()
        self.d_facts = tk.StringVar()
        ttk.Label(panel, textvariable=self.d_name, style="CardName.TLabel", wraplength=theme.px(306)).pack(anchor="w")
        self.signal_label = tk.Label(panel, textvariable=self.d_signal, background=theme.SURFACE,
                                     font=theme.font(13, "bold"), anchor="w")
        self.signal_label.pack(anchor="w", pady=(4, 2))
        ttk.Label(panel, textvariable=self.d_reason, style="Card.TLabel", wraplength=theme.px(306),
                  justify="left").pack(anchor="w")
        ttk.Label(panel, textvariable=self.d_facts, style="CardMuted.TLabel", wraplength=theme.px(306),
                  justify="left").pack(anchor="w", pady=(6, 4))

        self.d_ebay = tk.StringVar()
        ttk.Label(panel, textvariable=self.d_ebay, style="Card.TLabel", wraplength=theme.px(306),
                  justify="left").pack(anchor="w")
        ebay = ttk.Frame(panel, style="Header.TFrame")
        ebay.pack(anchor="w", pady=(6, 10))
        self.ebay_button = ttk.Button(ebay, text="eBay ↗", style="Small.TButton", command=self.open_ebay)
        self.ebay_button.pack(side="left")
        self.log_button = ttk.Button(ebay, text="Log sold price", style="Small.TButton", command=self.log_sold)
        self.log_button.pack(side="left", padx=(6, 6))
        self.site_var = tk.StringVar(value=self.db.get_setting("ebay_site", currency.ebay_site()))
        site = ttk.Combobox(ebay, textvariable=self.site_var, values=EBAY_SITES, state="readonly", width=11)
        site.pack(side="left")
        site.bind("<<ComboboxSelected>>", lambda _: self.db.set_setting("ebay_site", self.site_var.get()))

    # --- display -------------------------------------------------------------

    def _sort_value(self, r: market.MarketRow, key: str):
        """Value of a row for sorting; None sorts last in either direction."""
        known = r.trend is not None and r.trend.enough_data
        if key == "signal":
            return -market.SIGNAL_ORDER.index(r.signal)  # strongest signal counts as "biggest"
        if key == "name":
            return r.name.lower()
        if key == "owned":
            return r.owned
        if key == "value":
            return r.value or None
        if key == "ebay":
            sold = market.recent_sold(self.db.sold_prices(r.name))
            return sold[0] if sold else None
        if key == "profit":
            return r.profit
        if key == "price":
            return r.price_change.fraction if r.price_change else None
        if key == "play":
            return r.trend.recent_share if r.trend else None
        if key == "meta":
            return r.trend.change_points if known else None
        return None

    def sort_by(self, key: str) -> None:
        if self.sort_key == key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key, self.sort_reverse = key, key != "name"  # numbers: biggest first
        self.refresh()

    def _switch_view(self) -> None:
        catalog_view = self.view_var.get() == ALL_CARDS
        if catalog_view:
            # The tiles are about signals; the price list uses the room instead.
            self.tiles_frame.pack_forget()
            self.signal_body.pack_forget()
            self.signal_controls.pack_forget()
            self.all_cards.pack(fill="both", expand=True)
        else:
            self.tiles_frame.pack(fill="x", pady=(0, 14), before=self.controls_bar)
            self.all_cards.pack_forget()
            self.signal_controls.pack(side="left")
            self.signal_body.pack(fill="both", expand=True)
        self.refresh()

    def refresh(self) -> None:
        if self.view_var.get() == ALL_CARDS:
            self.all_cards.refresh()
        days = WINDOWS[self.window_var.get()]
        self.rows = market.analyse(self.db, self.meta, days=days)
        view = self.view_var.get()
        wanted = SIGNAL_FILTERS[self.signal_var.get()]
        shown = [(idx, r) for idx, r in enumerate(self.rows)
                 if (view == EVERYTHING or (view == MY_CARDS) == (r.owned > 0))
                 and (wanted is None or r.signal in wanted)]
        if self.sort_key:
            known = [x for x in shown if self._sort_value(x[1], self.sort_key) is not None]
            unknown = [x for x in shown if self._sort_value(x[1], self.sort_key) is None]
            known.sort(key=lambda x: self._sort_value(x[1], self.sort_key), reverse=self.sort_reverse)
            shown = known + unknown
        for key, heading, *_ in COLUMNS:
            arrow = (" ▼" if self.sort_reverse else " ▲") if key == self.sort_key else ""
            self.tree.heading(key, text=heading + arrow)
        self.tree.event_generate("<<Refit>>")

        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for i, (idx, r) in enumerate(shown):
            trend_known = r.trend is not None and r.trend.enough_data
            profit = r.profit
            sold = market.recent_sold(self.db.sold_prices(r.name))
            self.tree.insert("", "end", iid=str(idx),
                             tags=(SIGNAL_TAGS[r.signal], theme.row_tag(i)), values=[
                r.signal,
                r.name,
                r.owned or "",
                _money(r.value) if r.value else "",
                (currency.fmt_local(sold[0]) + (f" ({sold[1]})" if sold[1] > 1 else "")) if sold else "",
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
        if self.view_var.get() == ALL_CARDS:
            if not self.busy:
                updated = self.db.get_setting("catalog_updated")
                self.status.set(
                    f"Every Riftbound printing · TCGplayer market prices via riftbound.gg in {currency.code()}"
                    + (f", updated {_friendly_day(updated).lower()}" if updated else "")
                    + " · green/red = up/down 5%+ this week · click a heading to sort")
            return
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
            self.d_ebay.set("")
            self.ebay_button.state(["disabled"])
            self.log_button.state(["disabled"])
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

        self.ebay_button.state(["!disabled"])
        self.log_button.state(["!disabled"])
        sold = market.recent_sold(self.db.sold_prices(r.name))
        if not sold:
            self.d_ebay.set("eBay sold: none logged. Open eBay's sold listings and log a few prices.")
        else:
            avg, count, newest = sold
            text = (f"eBay sold: {currency.fmt_local(avg)}" + (f" (average of {count})" if count > 1 else "")
                    + f", latest {_friendly_day(newest).lower()}.")
            if r.value:
                diff = (currency.to_usd(avg) - r.value) / r.value
                if abs(diff) >= 0.05:
                    text += (f" {abs(diff):.0%} {'above' if diff > 0 else 'below'} TCGplayer"
                             + (", so eBay may pay more." if diff > 0 else "."))
                else:
                    text += " About the same as TCGplayer."
            self.d_ebay.set(text)

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

    def _selected_row(self) -> market.MarketRow | None:
        sel = self.tree.selection()
        return self.rows[int(sel[0])] if sel else None

    def open_ebay(self) -> None:
        r = self._selected_row()
        if r:
            webbrowser.open(market.ebay_sold_url(r.name, r.card.game if r.card else "Riftbound", self.site_var.get()))

    def log_sold(self) -> None:
        r = self._selected_row()
        if r and SoldPriceDialog(self.winfo_toplevel(), self.db, r.name).changed:
            self.refresh()

    def update_all_prices(self, silent: bool = False) -> None:
        """Refresh every collection and wishlist price, and save today's
        Riftbound market prices so meta cards build a price history too."""
        from .gui import run_in_background

        if self.busy:
            return
        cards = [c for c in self.db.search(wishlist=None) if pricing.supported(c)]

        def work(report):
            report("Downloading every Riftbound card's price…")
            cards_list, catalog_error = [], None
            try:
                cards_list = catalog.fetch()
            except riftboundgg.FetchError as e:
                catalog_error = str(e)
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
            return cards_list, catalog_error, snapshot, snapshot_error, found, failed

        def done(result, error):
            self.busy = False
            self.update_button.state(["!disabled"])
            if error:
                self.refresh()
                if not silent:
                    messagebox.showerror("Update prices", f"Price update stopped: {error}")
                return
            cards_list, catalog_error, snapshot, snapshot_error, found, failed = result
            if cards_list:
                catalog.save(self.db, cards_list)
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
                self.status.set(f"Prices updated: {len(cards_list):,} printings, {len(found)} of your cards."
                                + (f" Problem: {catalog_error}" if catalog_error else ""))
                return
            lines = [f"Downloaded current prices for {len(cards_list):,} Riftbound printings." if cards_list
                     else f"Card price list not downloaded: {catalog_error}",
                     f"Updated {len(found)} of {len(cards)} cards in your collection and wishlist."]
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
