"""The "Future insight" tab: which cards look like good investments, and which don't."""

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import catalog, currency, insight, market, theme
from .db import Card, CardDatabase, name_key
from .meta import MetaTracker

GOOD, BAD = insight.GOOD, insight.BAD

COLUMNS = [
    ("rating", "Verdict", 150, "w"),
    ("name", "Card", 170, "w"),
    ("price", "Price", 84, "e"),
    ("why", "Why", 250, "w"),
    ("owned", "Own", 56, "center"),
]


def _prices(db: CardDatabase) -> dict[str, float]:
    """Cheapest regular printing of each card, by name, from the day's price list."""
    prices: dict[str, float] = {}
    for c in catalog.load(db):
        if c.main_price and c.version in ("Standard", ""):
            key = name_key(c.base_name)
            prices[key] = min(prices.get(key, c.main_price), c.main_price)
    return prices


class InsightTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, db: CardDatabase, get_latest=None, on_data_changed=None):
        super().__init__(parent, padding=(16, 14, 16, 0))
        self.db = db
        self.meta = MetaTracker(db)
        self.get_latest = get_latest
        self.on_data_changed = on_data_changed or (lambda: None)
        self.result = insight.Investments()
        self.shown: list[insight.Pick] = []

        tiles = ttk.Frame(self)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {
            "good": theme.StatTile(tiles, "Good investments", gold=True),
            "bad": theme.StatTile(tiles, "Bad investments"),
            "sell": theme.StatTile(tiles, "Your cards to sell"),
            "decks": theme.StatTile(tiles, "Decklists analysed"),
        }
        for i, tile in enumerate(self.tiles.values()):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))
        self.kind_var = tk.StringVar(value=GOOD)
        for text, value in [("Good investments", GOOD), ("Bad investments", BAD)]:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.kind_var, style="Segment.TRadiobutton",
                            command=self._fill).pack(side="left")
        self.mine_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Only cards I own", variable=self.mine_var, command=self._fill).pack(
            side="left", padx=(16, 0))
        if get_latest:
            ttk.Button(bar, text="⬇  Get latest data", style="Accent.TButton", command=get_latest).pack(side="right")

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", pady=(10, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        details = ttk.Frame(body, style="Card.TFrame", padding=14, width=theme.px(336))
        details.pack(side="right", fill="y", padx=(12, 0))
        details.pack_propagate(False)
        self._build_details(details)

        table = ttk.Frame(body, style="Card.TFrame", padding=1)
        table.pack(side="left", fill="both", expand=True)
        frame = ttk.Frame(table, style="Header.TFrame")
        frame.pack(fill="both", expand=True)
        self.tree = theme.make_table(frame, COLUMNS, flexible=("name", "why"), selectmode="browse",
                                     fit_text={"rating": "★★★ Consider selling", "price": "£1,234.56"})
        self.tree.tag_configure(GOOD, foreground=theme.GOOD)
        self.tree.tag_configure(BAD, foreground=theme.BAD)
        self.tree.tag_configure("weak", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_details())
        self.refresh()

    def _build_details(self, panel: ttk.Frame) -> None:
        self.d_name, self.d_verdict, self.d_price = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(panel, textvariable=self.d_name, style="CardName.TLabel", wraplength=theme.px(306)).pack(anchor="w")
        self.verdict_label = ttk.Label(panel, textvariable=self.d_verdict, style="CardSection.TLabel",
                                       font=theme.font(15, "bold"))
        self.verdict_label.pack(anchor="w", pady=(4, 0))
        ttk.Label(panel, textvariable=self.d_price, style="CardMuted.TLabel").pack(anchor="w", pady=(0, 8))
        self.reasons_frame = ttk.Frame(panel, style="Header.TFrame")
        self.reasons_frame.pack(fill="both", expand=True, anchor="n")
        buttons = ttk.Frame(panel, style="Header.TFrame")
        buttons.pack(side="bottom", fill="x", pady=(8, 0))
        buttons.columnconfigure((0, 1), weight=1, uniform="b")
        self.wish_button = ttk.Button(buttons, text="★ Add to wishlist", style="Small.TButton",
                                      command=self.add_to_wishlist)
        self.wish_button.grid(row=0, column=0, sticky="ew")
        self.ebay_button = ttk.Button(buttons, text="eBay sold ↗", style="Small.TButton", command=self.open_ebay)
        self.ebay_button.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        ttk.Label(panel, style="CardMuted.TLabel", wraplength=theme.px(306), justify="left", text=(
            "These are signs worth checking, not guarantees: prices can move for reasons no data shows."
        )).pack(side="bottom", anchor="w", pady=(8, 0))

    # --- display -------------------------------------------------------------

    def refresh(self) -> None:
        self.result = insight.investments(self.db, self.meta, _prices(self.db))
        r = self.result
        self.tiles["good"].value.set(str(len(r.good)))
        self.tiles["bad"].value.set(str(len(r.bad)))
        self.tiles["sell"].value.set(str(sum(1 for p in r.bad if p.owned)))
        self.tiles["decks"].value.set(f"{r.decks:,}")
        self.status.set(
            (r.message + "  ") if r.message and not (r.good or r.bad) else
            f"Based on {r.decks:,} recent decklists and the day's prices. Click a card to see why. "
            "Get latest data adds new tournaments and prices.")
        self._fill()

    def _fill(self) -> None:
        picks = self.result.good if self.kind_var.get() == GOOD else self.result.bad
        self.shown = [p for p in picks if not self.mine_var.get() or p.owned]
        self.tree.delete(*self.tree.get_children())
        for i, p in enumerate(self.shown):
            tag = "weak" if p.strength == 1 else p.kind
            self.tree.insert("", "end", iid=str(i), tags=(tag, theme.row_tag(i)), values=[
                f"{p.stars}  {p.verdict}", p.name, currency.fmt(p.price) if p.price else "",
                p.reasons[0].short if p.reasons else "", p.owned or "",
            ])
        if self.shown:
            self.tree.selection_set("0")
        self.show_details()

    def show_details(self) -> None:
        for child in self.reasons_frame.winfo_children():
            child.destroy()
        sel = self.tree.selection()
        if not sel:
            good = self.kind_var.get() == GOOD
            self.d_name.set("Nothing here yet" if not self.shown else "Select a card")
            self.d_verdict.set("")
            self.d_price.set("")
            ttk.Label(self.reasons_frame, style="Card.TLabel", wraplength=theme.px(306), justify="left", text=(
                self.result.message or
                ("No card shows clear signs of being a good buy right now." if good else
                 "No card shows clear signs of being a bad investment right now.")
                + " Check again after the next tournaments.")).pack(anchor="w")
            self.wish_button.state(["disabled"])
            self.ebay_button.state(["disabled"])
            return
        p = self.shown[int(sel[0])]
        self.d_name.set(p.name)
        self.d_verdict.set(f"{p.stars}  {p.verdict}")
        self.verdict_label.configure(foreground=theme.GOOD if p.kind == GOOD else theme.BAD)
        self.d_price.set(" · ".join(filter(None, [
            f"About {currency.fmt(p.price)} a copy" if p.price else "",
            f"you own {p.owned}" if p.owned else ""])))
        ttk.Label(self.reasons_frame, text="Why", style="CardSection.TLabel").pack(anchor="w")
        for reason in p.reasons:
            ttk.Label(self.reasons_frame, text=f"▸ {reason.short}", style="Card.TLabel",
                      font=theme.font(10, "bold")).pack(anchor="w", pady=(6, 0))
            ttk.Label(self.reasons_frame, text=reason.detail, style="CardMuted.TLabel", wraplength=theme.px(286),
                      justify="left").pack(anchor="w", padx=(14, 0))
        self.wish_button.state(["!disabled"] if p.kind == GOOD else ["disabled"])
        self.ebay_button.state(["!disabled"])

    # --- actions -------------------------------------------------------------

    def _selected(self) -> insight.Pick | None:
        sel = self.tree.selection()
        return self.shown[int(sel[0])] if sel else None

    def add_to_wishlist(self) -> None:
        p = self._selected()
        if not p:
            return
        if any(name_key(c.name) == name_key(p.name) for c in self.db.search(wishlist=True)):
            messagebox.showinfo("Add to wishlist", f"{p.name} is already on your wishlist.")
            return
        self.db.add(Card(name=p.name, game="Riftbound", wishlist=True, value=p.price,
                         notes=f"Added from Future insight ({p.verdict}: {p.reasons[0].short if p.reasons else ''})"))
        self.on_data_changed()
        messagebox.showinfo("Add to wishlist", f"Added {p.name} to your wishlist.")

    def open_ebay(self) -> None:
        p = self._selected()
        if p:
            webbrowser.open(market.ebay_sold_url(p.name, "Riftbound", self.db.get_setting("ebay_site", currency.ebay_site())))
